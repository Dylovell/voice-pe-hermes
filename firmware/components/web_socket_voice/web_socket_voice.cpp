#include "web_socket_voice.h"
#include "esphome/core/application.h"

namespace esphome {
namespace web_socket_voice {

static const char *const TAG = "web_socket_voice";

// ── Constants ─────────────────────────────────────────────────────────

/// Microphone sample rate (Hz).  Shorter buffers = lower latency.
constexpr uint32_t MIC_SAMPLE_RATE = 16000;
/// Microchannel bit depth.
constexpr uint8_t MIC_BITS_PER_SAMPLE = 16;
/// Number of mic channels.
constexpr uint8_t MIC_CHANNELS = 1;
/// Speaker sample rate (Hz).
constexpr uint32_t SPK_SAMPLE_RATE = 48000;
/// Speaker bit depth.
constexpr uint8_t SPK_BITS_PER_SAMPLE = 16;
/// Max TTS audio size we'll buffer (5 MB).
constexpr size_t MAX_TTS_BUFFER = 5 * 1024 * 1024;
/// How many audio frames between silence checks.
constexpr size_t SILENCE_CHECK_INTERVAL = 10;
/// RMS threshold below which audio is considered silence.
constexpr float SILENCE_THRESHOLD = 0.005f;
/// Reconnect delay on connection failure (ms).
constexpr uint32_t RECONNECT_DELAY_MS = 3000;

// ── Component lifecycle ──────────────────────────────────────────────

void WebSocketVoice::setup() {
  ESP_LOGI(TAG, "Setting up WebSocket Voice component");
  ESP_LOGI(TAG, "Server: %s:%d", host_.c_str(), port_);

  // Start in disconnected state; the loop() will attempt to connect.
  set_state_(VoiceState::IDLE);
}

void WebSocketVoice::loop() {
  // Let the WebSocket client process incoming data.
  ws_.loop();

  const uint32_t now = millis();

  switch (state_) {
    case VoiceState::IDLE:
      // Connect to server if not already connected
      if (!ws_.isConnected()) {
        set_state_(VoiceState::CONNECTING);
        connect_ws();
      }
      break;

    case VoiceState::CONNECTING:
      // Wait for connection, then start streaming if requested
      if (ws_.isConnected()) {
        ESP_LOGI(TAG, "WebSocket connected to %s:%d", host_.c_str(), port_);
        set_state_(VoiceState::CONNECTED);
      }
      break;

    case VoiceState::STREAMING_MIC:
      // Check for utterance timeout
      if (now - stream_start_ms_ > max_utterance_ms_) {
        ESP_LOGD(TAG, "Utterance timeout reached (%d ms)", max_utterance_ms_);
        stop_stream();
        break;
      }
      // Check for silence timeout while streaming
      if (now - last_speech_ms_ > silence_timeout_ms_ &&
          audio_buffer_.size() > MIC_SAMPLE_RATE) {
        ESP_LOGD(TAG, "Silence timeout reached, ending utterance");
        stop_stream();
      }
      break;

    case VoiceState::PLAYING_TTS:
      // Feed audio to the speaker
      if (spk_ != nullptr && tts_play_offset_ < tts_buffer_.size()) {
        size_t chunk_size = 1024; // bytes per write
        if (chunk_size > tts_buffer_.size() - tts_play_offset_) {
          chunk_size = tts_buffer_.size() - tts_play_offset_;
        }
        if (chunk_size > 0) {
          spk_->play(tts_buffer_.data() + tts_play_offset_, chunk_size);
          tts_play_offset_ += chunk_size;
          last_activity_ms_ = now;
        }
      } else {
        // Finished playing TTS
        if (spk_ != nullptr) {
          spk_->stop();
        }
        tts_buffer_.clear();
        tts_play_offset_ = 0;
        ESP_LOGI(TAG, "TTS playback complete");
        set_state_(VoiceState::CONNECTED);
      }
      break;

    default:
      break;
  }
}

// ── WebSocket ─────────────────────────────────────────────────────────

void WebSocketVoice::connect_ws() {
  ESP_LOGI(TAG, "Connecting to %s:%d ...", host_.c_str(), port_);

  ws_.begin(host_.c_str(), port_, "/");

  ws_.onEvent([this](WStype_t type, uint8_t *payload, size_t length) {
    this->on_ws_event(type, payload, length);
  });

  // Use the ESP32's built-in SSL if needed
  ws_.setReconnectInterval(RECONNECT_DELAY_MS);
}

void WebSocketVoice::disconnect_ws() {
  ESP_LOGI(TAG, "Disconnecting WebSocket");
  ws_.disconnect();
}

void WebSocketVoice::send_audio_chunk(const uint8_t *data, size_t len) {
  if (ws_.isConnected()) {
    ws_.sendBIN(data, len);
  }
}

void WebSocketVoice::send_json(const JsonDocument &doc) {
  if (!ws_.isConnected())
    return;

  std::string json;
  serializeJson(doc, json);
  ws_.sendTXT(json);
}

void WebSocketVoice::on_ws_event(WStype_t type, uint8_t *payload,
                                  size_t length) {
  switch (type) {
    case WStype_DISCONNECTED:
      ESP_LOGW(TAG, "WebSocket disconnected");
      set_state_(VoiceState::IDLE);
      break;

    case WStype_CONNECTED:
      ESP_LOGI(TAG, "WebSocket connected");
      // If we were waiting to stream, start now
      if (state_ == VoiceState::CONNECTING) {
        set_state_(VoiceState::CONNECTED);
      }
      break;

    case WStype_TEXT: {
      // JSON message from server
      JsonDocument doc;
      DeserializationError err = deserializeJson(doc, payload, length);
      if (err) {
        ESP_LOGW(TAG, "JSON parse error: %s", err.c_str());
        return;
      }

      const char *type_str = doc["type"];
      if (type_str == nullptr)
        return;

      if (strcmp(type_str, "speaking_start") == 0) {
        // Server is about to send TTS audio
        ESP_LOGI(TAG, "Server: speaking start");
        tts_buffer_.clear();
        tts_play_offset_ = 0;
        set_state_(VoiceState::WAITING_FOR_TTS);
      } else if (strcmp(type_str, "speaking_end") == 0) {
        // TTS finished, flush any remaining audio
        if (spk_ != nullptr && tts_play_offset_ < tts_buffer_.size()) {
          spk_->play(tts_buffer_.data() + tts_play_offset_,
                     tts_buffer_.size() - tts_play_offset_);
        }
        if (spk_ != nullptr) {
          spk_->stop();
        }
        tts_buffer_.clear();
        tts_play_offset_ = 0;
        ESP_LOGI(TAG, "Server: speaking end");
        set_state_(VoiceState::CONNECTED);
      } else if (strcmp(type_str, "pong") == 0) {
        ESP_LOGV(TAG, "Pong received");
      } else if (strcmp(type_str, "error") == 0) {
        const char *msg = doc["message"];
        ESP_LOGW(TAG, "Server error: %s", msg ? msg : "unknown");
        set_state_(VoiceState::ERROR_STATE);
      }
      break;
    }

    case WStype_BIN: {
      // Binary data from server = TTS audio chunk
      if (state_ == VoiceState::WAITING_FOR_TTS ||
          state_ == VoiceState::PLAYING_TTS) {
        if (tts_buffer_.size() + length <= MAX_TTS_BUFFER) {
          tts_buffer_.insert(tts_buffer_.end(), payload, payload + length);
          set_state_(VoiceState::PLAYING_TTS);
        } else {
          ESP_LOGW(TAG, "TTS buffer full, dropping %d bytes", length);
        }
      }
      break;
    }

    default:
      break;
  }
}

// ── Audio handling ───────────────────────────────────────────────────

void WebSocketVoice::on_mic_data_(const std::vector<int16_t> &data) {
  if (state_ != VoiceState::STREAMING_MIC)
    return;

  // Add to local buffer
  audio_buffer_.insert(audio_buffer_.end(), data.begin(), data.end());

  // Calculate RMS energy for VAD
  float rms = 0.0f;
  for (size_t i = 0; i < data.size(); i++) {
    float sample = data[i] / 32768.0f;
    rms += sample * sample;
  }
  rms = sqrtf(rms / data.size());

  if (rms > SILENCE_THRESHOLD) {
    last_speech_ms_ = millis();
  }

  // Send audio to server as raw 16-bit PCM
  send_audio_chunk(reinterpret_cast<const uint8_t *>(data.data()),
                   data.size() * sizeof(int16_t));
}

// ── State management ──────────────────────────────────────────────────

void WebSocketVoice::set_state_(VoiceState new_state) {
  VoiceState old_state = state_;
  state_ = new_state;

  if (old_state == new_state)
    return;

  ESP_LOGD(TAG, "State: %d → %d", static_cast<int>(old_state),
           static_cast<int>(new_state));
}

// ── Public API ────────────────────────────────────────────────────────

void WebSocketVoice::start_stream() {
  if (mic_ == nullptr) {
    ESP_LOGE(TAG, "Cannot start stream: no microphone configured");
    return;
  }

  if (state_ == VoiceState::STREAMING_MIC)
    return;

  // If we're currently playing TTS, the server handles barge-in
  if (state_ == VoiceState::PLAYING_TTS) {
    ESP_LOGI(TAG, "Barge-in: interrupting TTS playback");
    tts_buffer_.clear();
    tts_play_offset_ = 0;
    if (spk_ != nullptr) {
      spk_->stop();
    }
  }

  ESP_LOGI(TAG, "Starting microphone stream");

  audio_buffer_.clear();
  stream_start_ms_ = millis();
  last_speech_ms_ = stream_start_ms_;

  // Notify server
  JsonDocument doc;
  doc["type"] = "utterance_start";
  send_json(doc);

  // Start microphone
  mic_->start();
  mic_->set_data_callback(
      [this](const std::vector<int16_t> &data) { on_mic_data_(data); });

  set_state_(VoiceState::STREAMING_MIC);
}

void WebSocketVoice::stop_stream() {
  if (state_ != VoiceState::STREAMING_MIC)
    return;

  ESP_LOGI(TAG, "Stopping microphone stream (%d samples buffered)",
           audio_buffer_.size());

  // Stop mic
  if (mic_ != nullptr) {
    mic_->stop();
  }

  // Notify server
  JsonDocument doc;
  doc["type"] = "utterance_end";
  send_json(doc);

  set_state_(VoiceState::CONNECTED);
}

}  // namespace web_socket_voice
}  // namespace esphome
