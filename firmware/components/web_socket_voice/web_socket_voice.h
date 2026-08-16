#pragma once

#include "esphome/core/component.h"
#include "esphome/core/helpers.h"
#include "esphome/core/log.h"
#include "esphome/components/microphone/microphone.h"
#include "esphome/components/speaker/speaker.h"

#include "esp_websocket_client.h"
#include "cJSON.h"

namespace esphome {
namespace web_socket_voice {

/// State machine for the WebSocket voice connection.
enum class VoiceState : uint8_t {
  IDLE = 0,
  CONNECTING,
  CONNECTED,
  STREAMING_MIC,
  WAITING_FOR_TTS,
  PLAYING_TTS,
  ERROR_STATE,
};

class WebSocketVoice : public Component {
 public:
  // ── Configuration setters ──────────────────────────────────────────
  void set_server_host(const std::string &host) { host_ = host; }
  void set_server_port(uint16_t port) { port_ = port; }

  void set_microphone(microphone::Microphone *mic) { mic_ = mic; }
  void set_speaker(speaker::Speaker *spk) { spk_ = spk; }

  // ── Component lifecycle ────────────────────────────────────────────
  void setup() override;
  void loop() override;
  float get_setup_priority() const override {
    return setup_priority::AFTER_WIFI;
  }

  // ── Public API ─────────────────────────────────────────────────────
  /// Start listening (triggered by wake word or button press).
  void start_stream();
  /// Stop listening (VAD timeout or manual stop).
  void stop_stream();

  bool is_streaming() const { return state_ == VoiceState::STREAMING_MIC; }
  bool is_speaking() const { return state_ == VoiceState::PLAYING_TTS; }
  bool is_connected() const { return state_ != VoiceState::IDLE && state_ != VoiceState::CONNECTING && state_ != VoiceState::ERROR_STATE; }

 protected:
  // ── WebSocket ──────────────────────────────────────────────────────
  void connect_ws();
  void disconnect_ws();
  void send_audio_chunk(const uint8_t *data, size_t len);
  void send_json(const char *json_str);
  static void ws_event_handler_(void *handler_args, esp_event_base_t base,
                                 int32_t event_id, void *event_data);

  // ── Audio handling ─────────────────────────────────────────────────
  void on_mic_data_(const std::vector<uint8_t> &data);

  // ── State management ───────────────────────────────────────────────
  void set_state_(VoiceState new_state);

  std::string host_;
  uint16_t port_{8765};

  VoiceState state_{VoiceState::IDLE};
  esp_websocket_client_handle_t ws_client_{nullptr};

  microphone::Microphone *mic_{nullptr};
  speaker::Speaker *spk_{nullptr};

  /// Buffer for audio data while listening (raw PCM bytes).
  std::vector<uint8_t> audio_buffer_;

  /// Buffer for incoming TTS audio (raw PCM).
  std::vector<uint8_t> tts_buffer_;
  size_t tts_play_offset_{0};

  /// Timestamps for timeout detection (ms).
  uint32_t stream_start_ms_{0};
  uint32_t last_speech_ms_{0};
  /// Timestamp of the last WebSocket connection attempt (ms).
  uint32_t last_connect_ms_{0};

  /// Maximum utterance length (default 30s).
  uint32_t max_utterance_ms_{5000};
  /// Silence timeout (stop after this much silence).
  uint32_t silence_timeout_ms_{1500};

  /// Flag: has the mic data callback already been registered?
  bool mic_callback_added_{false};

  /// Flag: WebSocket disconnected while in websocket task; main loop
  /// must destroy the handle (don't call destroy/stop from event handler).
  bool ws_needs_reconnect_{false};
  /// Timestamp of last reconnect attempt (ms).
  uint32_t last_reconnect_ms_{0};
  /// Maximum reconnect backoff delay (ms).
  uint32_t max_reconnect_delay_ms_{30000};
};

}  // namespace web_socket_voice
}  // namespace esphome
