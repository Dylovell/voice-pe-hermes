#include "web_socket_voice.h"
#include "esphome/core/application.h"
#include "esphome/components/wifi/wifi_component.h"
#include "esphome/components/script/script.h"

#include <cstring>
#include <string>
#include <vector>

// ═══════════════════════════════════════════════
// LED integration with stock control_leds script
// ═══════════════════════════════════════════════
// Pointers are set from YAML via set_voice_assistant_phase() and
// set_control_leds() — no extern declarations needed.  The stock
// package declares these as `static` so we access them through
// member pointers wired by ESPHome codegen (see __init__.py).

// Phase IDs from the stock Voice PE package (must match substitutions):
//   voice_assist_idle_phase_id = "1"
//   voice_assist_listening_phase_id = "3"
//   voice_assist_thinking_phase_id = "4"
//   voice_assist_replying_phase_id = "5"
// These are NOT extern symbols — they are YAML template substitutions
// inlined at compile time.  We hard-code the numeric values here.
static constexpr uint8_t VA_PHASE_IDLE = 1;
static constexpr uint8_t VA_PHASE_LISTENING = 3;
static constexpr uint8_t VA_PHASE_THINKING = 4;
static constexpr uint8_t VA_PHASE_REPLYING = 5;

// ── LED helper macros ─────────────────────────
// Guarded by null-checks — pointers are only set if YAML wires them.
// Takes an explicit `obj` parameter (either `this` or a `self` pointer)
// because LED_SET_PHASE/LED_RUN_SCRIPT are also used in static functions
// (ws_event_handler_) that cannot access members via implicit `this`.
#define LED_SET_PHASE(obj, phase)                     \
  do {                                                 \
    if ((obj)->voice_assistant_phase_ != nullptr)       \
      (obj)->voice_assistant_phase_->value() = (phase); \
  } while (0)
#define LED_RUN_SCRIPT(obj)                           \
  do {                                                 \
    if ((obj)->control_leds_ != nullptr)                \
      (obj)->control_leds_->execute();                  \
  } while (0)

namespace esphome {
namespace web_socket_voice {

static const char *const TAG = "web_socket_voice";

// ── Constants ─────────────────────────────────────────────────────────

constexpr uint32_t MIC_SAMPLE_RATE = 16000;
constexpr float SILENCE_THRESHOLD = 0.02f;
constexpr size_t AUDIO_CHUNK_SIZE = 512;  // bytes per mic callback chunk
constexpr uint32_t RECONNECT_DELAY_MS = 5000;

// ── Component lifecycle ──────────────────────────────────────────────

void WebSocketVoice::setup() {
  ESP_LOGI(TAG, "Setting up WebSocket Voice component");
  ESP_LOGI(TAG, "Server: %s:%d", host_.c_str(), port_);
  set_state_(VoiceState::IDLE);
}

void WebSocketVoice::loop() {
  const uint32_t now = millis();

  switch (state_) {
    case VoiceState::IDLE:
      // First connection — wait for WiFi before attempting WebSocket.
      // This avoids an immediate connection failure (and spinlock assert
      // on old ESP-IDF websocket client) when lwip has no route yet.
      if (!ws_client_) {
        if (wifi::global_wifi_component->is_connected()) {
          ESP_LOGI(TAG, "Initiating WebSocket connection");
          connect_ws();
          set_state_(VoiceState::CONNECTING);
        }
        break;
      }
      // Stale client handle from a prior disconnect — destroy from the
      // main loop context (safe, not from websocket task).
      if (ws_needs_reconnect_) {
        ws_needs_reconnect_ = false;
        ESP_LOGD(TAG, "Destroying stale WebSocket client handle");
        esp_websocket_client_destroy(ws_client_);
        ws_client_ = nullptr;
      }
      break;

    case VoiceState::CONNECTING:
      // Wait — esp_websocket_client has already exited (we used
      // disable_auto_reconnect=true), so WEBSOCKET_EVENT_CONNECTED
      // or WEBSOCKET_EVENT_DISCONNECTED will fire synchronously.
      break;

    case VoiceState::CONNECTED:
      // Idle — waiting for button press or wake word to call start_stream().
      break;

    case VoiceState::STREAMING_MIC:
      // Check utterance timeout
      if (now - stream_start_ms_ > max_utterance_ms_) {
        ESP_LOGD(TAG, "Utterance timeout (%d ms)", max_utterance_ms_);
        stop_stream();
        break;
      }
      // Check silence timeout
      if (now - last_speech_ms_ > silence_timeout_ms_ &&
          audio_buffer_.size() > MIC_SAMPLE_RATE * 2) {
        ESP_LOGD(TAG, "Silence timeout, ending utterance");
        stop_stream();
      }
      break;

    case VoiceState::PLAYING_TTS:
      // Feed audio to speaker
      if (spk_ != nullptr && tts_play_offset_ < tts_buffer_.size()) {
        size_t remaining = tts_buffer_.size() - tts_play_offset_;
        size_t chunk = (remaining > 1024) ? 1024 : remaining;
        spk_->play(tts_buffer_.data() + tts_play_offset_, chunk);
        tts_play_offset_ += chunk;
      } else {
        // Finished
        if (spk_ != nullptr) {
          spk_->stop();
        }
        tts_buffer_.clear();
        tts_play_offset_ = 0;
        ESP_LOGI(TAG, "TTS playback complete");
        set_state_(VoiceState::CONNECTED);

        // Update LED to idle animation
        LED_SET_PHASE(this, VA_PHASE_IDLE);
        LED_RUN_SCRIPT(this);

        // Send json speaking_end
        send_json(R"({"type":"speaking_end"})");
      }
      break;

    default:
      break;
  }
}

// ── WebSocket ─────────────────────────────────────────────────────────

void WebSocketVoice::connect_ws() {
  char uri[128];
  snprintf(uri, sizeof(uri), "ws://%s:%d/", host_.c_str(), port_);
  ESP_LOGI(TAG, "Connecting to %s", uri);

  esp_websocket_client_config_t cfg = {};
  cfg.uri = uri;
  cfg.buffer_size = 4096;
  cfg.task_stack = 8192;
  // CRITICAL: disable auto-reconnect.  When the connection fails the
  // websocket task exits immediately (instead of entering a 10 s
  // WAIT_TIMEOUT).  This lets our loop() safely call destroy() without
  // blocking or contending for spinlocks across core boundaries.
  cfg.disable_auto_reconnect = true;

  ws_client_ = esp_websocket_client_init(&cfg);
  esp_websocket_register_events(ws_client_, WEBSOCKET_EVENT_ANY,
                                ws_event_handler_, this);

  last_connect_ms_ = millis();
  esp_websocket_client_start(ws_client_);
}

void WebSocketVoice::disconnect_ws() {
  if (ws_client_) {
    esp_websocket_client_stop(ws_client_);
    esp_websocket_client_destroy(ws_client_);
    ws_client_ = nullptr;
  }
}

void WebSocketVoice::send_audio_chunk(const uint8_t *data, size_t len) {
  if (ws_client_ && esp_websocket_client_is_connected(ws_client_)) {
    esp_websocket_client_send_bin(ws_client_, (const char *)data, len,
                                  portMAX_DELAY);
  }
}

void WebSocketVoice::send_json(const char *json_str) {
  if (ws_client_ && esp_websocket_client_is_connected(ws_client_)) {
    esp_websocket_client_send_text(ws_client_, json_str, strlen(json_str),
                                   portMAX_DELAY);
  }
}

void WebSocketVoice::ws_event_handler_(void *handler_args,
                                        esp_event_base_t base,
                                        int32_t event_id, void *event_data) {
  auto *self = static_cast<WebSocketVoice *>(handler_args);
  auto *data = static_cast<esp_websocket_event_data_t *>(event_data);

  switch (event_id) {
    case WEBSOCKET_EVENT_CONNECTED:
      ESP_LOGI(TAG, "WebSocket connected");
      // Don't overwrite streaming or playing states
      if (self->state_ != VoiceState::STREAMING_MIC &&
          self->state_ != VoiceState::WAITING_FOR_TTS &&
          self->state_ != VoiceState::PLAYING_TTS) {
        self->set_state_(VoiceState::CONNECTED);
      }
      break;

    case WEBSOCKET_EVENT_DISCONNECTED:
      ESP_LOGW(TAG, "WebSocket disconnected");
      // NOTE: Do NOT call esp_websocket_client_destroy() here — we're
      // running in the websocket task's event handler and destroy()
      // internally calls stop() which tries to join itself, causing a
      // spinlock deadlock.  Instead, flag the main loop to destroy
      // the handle from the safe main-loop context.
      self->ws_needs_reconnect_ = true;
      self->set_state_(VoiceState::IDLE);
      break;

    case WEBSOCKET_EVENT_DATA: {
      if (data->op_code == 1) {
        // Text frame — JSON from server
        ::std::string msg((const char *)data->data_ptr, data->data_len);

        cJSON *root = cJSON_Parse(msg.c_str());
        if (!root) {
          ESP_LOGW(TAG, "JSON parse error");
          break;
        }

        cJSON *type = cJSON_GetObjectItem(root, "type");
        if (type && type->valuestring) {
          if (strcmp(type->valuestring, "speaking_start") == 0) {
            ESP_LOGI(TAG, "Server: speaking_start");
            self->tts_buffer_.clear();
            self->tts_play_offset_ = 0;
            self->set_state_(VoiceState::WAITING_FOR_TTS);
            // Update LED to "replying" animation
            LED_SET_PHASE(self, VA_PHASE_REPLYING);
            LED_RUN_SCRIPT(self);
          } else if (strcmp(type->valuestring, "speaking_end") == 0) {
            // Server finished streaming. Don't stop or clear — let the
            // speaker finish the buffered audio naturally.
            ESP_LOGI(TAG, "Server: speaking_end (playback continues)");
          } else if (strcmp(type->valuestring, "pong") == 0) {
            // keep-alive response
          } else if (strcmp(type->valuestring, "error") == 0) {
            cJSON *msg_json = cJSON_GetObjectItem(root, "message");
            ESP_LOGW(TAG, "Server error: %s",
                     msg_json ? msg_json->valuestring : "unknown");
          }
        }

        cJSON_Delete(root);
      } else if (data->op_code == 2) {
        // Binary frame — TTS audio data
        if (self->state_ == VoiceState::WAITING_FOR_TTS ||
            self->state_ == VoiceState::PLAYING_TTS) {
          size_t prev_size = self->tts_buffer_.size();
          self->tts_buffer_.resize(prev_size + data->data_len);
          memcpy(self->tts_buffer_.data() + prev_size, data->data_ptr,
                 data->data_len);
          self->set_state_(VoiceState::PLAYING_TTS);
        }
      }
      break;
    }

    case WEBSOCKET_EVENT_ERROR:
      ESP_LOGW(TAG, "WebSocket error");
      break;

    default:
      break;
  }
}

// ── Audio handling ───────────────────────────────────────────────────

void WebSocketVoice::on_mic_data_(const ::std::vector<uint8_t> &data) {
  if (state_ != VoiceState::STREAMING_MIC)
    return;

  // Buffer locally — audio is 16-bit PCM delivered as uint8_t bytes
  audio_buffer_.insert(audio_buffer_.end(), data.begin(), data.end());

  // Simple energy-based VAD (treat pairs of bytes as int16 samples)
  float rms = 0.0f;
  size_t sample_count = data.size() / 2;
  const int16_t *samples = reinterpret_cast<const int16_t *>(data.data());
  for (size_t i = 0; i < sample_count; i++) {
    float sample = samples[i] / 32768.0f;
    rms += sample * sample;
  }
  rms = sqrtf(rms / (sample_count > 0 ? sample_count : 1));

  if (rms > SILENCE_THRESHOLD) {
    last_speech_ms_ = millis();
  }

  // Stream to server
  send_audio_chunk(data.data(), data.size());
}

// ── State management ──────────────────────────────────────────────────

void WebSocketVoice::set_state_(VoiceState new_state) {
  if (state_ == new_state)
    return;
  VoiceState old_state = state_;
  state_ = new_state;
  ESP_LOGD(TAG, "State: %d -> %d", static_cast<int>(old_state),
           static_cast<int>(new_state));
}

// ── Public API ────────────────────────────────────────────────────────

void WebSocketVoice::start_stream() {
  if (mic_ == nullptr) {
    ESP_LOGE(TAG, "start_stream() called but NO MICROPHONE configured!");
    return;
  }

  if (state_ == VoiceState::STREAMING_MIC) {
    ESP_LOGV(TAG, "start_stream() already streaming, ignoring");
    return;
  }

  // Barge-in: if playing TTS, stop
  if (state_ == VoiceState::PLAYING_TTS) {
    ESP_LOGI(TAG, "Barge-in: interrupting TTS");
    tts_buffer_.clear();
    tts_play_offset_ = 0;
    if (spk_ != nullptr) {
      spk_->stop();
    }
  }

  ESP_LOGI(TAG, "STARTING microphone stream (state=%d)", static_cast<int>(state_));

  audio_buffer_.clear();
  stream_start_ms_ = millis();
  last_speech_ms_ = stream_start_ms_;

  // Update LED to "listening" animation
  LED_SET_PHASE(this, VA_PHASE_LISTENING);
  LED_RUN_SCRIPT(this);

  // Notify server
  send_json(R"({"type":"utterance_start"})");

  // Add mic data callback only once
  if (!mic_callback_added_) {
    mic_->add_data_callback(
        [this](const auto &data) { on_mic_data_(data); });
    mic_callback_added_ = true;
  }

  set_state_(VoiceState::STREAMING_MIC);
}

void WebSocketVoice::stop_stream() {
  if (state_ != VoiceState::STREAMING_MIC)
    return;

  ESP_LOGI(TAG, "Stopping microphone stream (%d samples)",
           audio_buffer_.size());

  send_json(R"({"type":"utterance_end"})");
  set_state_(VoiceState::CONNECTED);

  // Reset LED phase back to idle
  LED_SET_PHASE(this, VA_PHASE_IDLE);
  LED_RUN_SCRIPT(this);
}

}  // namespace web_socket_voice
}  // namespace esphome
