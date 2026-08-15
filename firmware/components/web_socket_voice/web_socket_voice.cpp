#include "web_socket_voice.h"
#include "esphome/core/application.h"

#include <cstring>

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
      // Connect to server
      connect_ws();
      set_state_(VoiceState::CONNECTING);
      break;

    case VoiceState::CONNECTING:
      // Wait for connection callback to change state
      break;

    case VoiceState::CONNECTED:
      // Auto-start streaming after 3s (for testing — remove when button works)
      if (now > 3000 && !auto_started_) {
        auto_started_ = true;
        ESP_LOGI(TAG, "Auto-trigger: start_stream()");
        start_stream();
      }
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

  ws_client_ = esp_websocket_client_init(&cfg);
  esp_websocket_register_events(ws_client_, WEBSOCKET_EVENT_ANY,
                                ws_event_handler_, this);

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
      // Destroy client handle to avoid memory leak on reconnect.
      // NOTE: Do NOT call esp_websocket_client_stop() here — we're
      // running in the websocket task's event handler and stop()
      // tries to join itself, causing a spinlock deadlock.
      if (self->ws_client_) {
        esp_websocket_client_destroy(self->ws_client_);
        self->ws_client_ = nullptr;
      }
      self->set_state_(VoiceState::IDLE);
      break;

    case WEBSOCKET_EVENT_DATA: {
      if (data->op_code == 1) {
        // Text frame — JSON from server
        std::string msg((const char *)data->data_ptr, data->data_len);

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

void WebSocketVoice::on_mic_data_(const std::vector<uint8_t> &data) {
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

  // Notify server
  send_json(R"({"type":"utterance_start"})");

  // Add mic data callback only once
  if (!mic_callback_added_) {
    mic_->add_data_callback(
        [this](const std::vector<uint8_t> &data) { on_mic_data_(data); });
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

  // Reset auto-start so the cycle continues
  // (allows always-listening mode)
  auto_started_ = false;
}

}  // namespace web_socket_voice
}  // namespace esphome
