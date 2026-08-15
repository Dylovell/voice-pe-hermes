#pragma once

#include "esphome/core/component.h"
#include "esphome/core/helpers.h"
#include "esphome/core/log.h"
#include "esphome/components/microphone/microphone.h"
#include "esphome/components/speaker/speaker.h"

#include <ArduinoJson.h>
#include <WebSocketsClient.h>

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
  // ── Configuration ──────────────────────────────────────────────────
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
  bool is_connected() const { return ws_.isConnected(); }

 protected:
  // ── WebSocket ──────────────────────────────────────────────────────
  void connect_ws();
  void disconnect_ws();
  void send_audio_chunk(const uint8_t *data, size_t len);
  void send_json(const JsonDocument &doc);
  void on_ws_event(WStype_t type, uint8_t *payload, size_t length);

  // ── Audio handling ─────────────────────────────────────────────────
  /// Called by the mic's data callback.
  void on_mic_data_(const std::vector<int16_t> &data);

  // ── State management ───────────────────────────────────────────────
  void set_state_(VoiceState new_state);

  std::string host_;
  uint16_t port_{8765};

  VoiceState state_{VoiceState::IDLE};
  WebSocketsClient ws_;

  microphone::Microphone *mic_{nullptr};
  speaker::Speaker *spk_{nullptr};

  /// Buffer for audio data while listening (raw 16-bit PCM, 16 kHz).
  std::vector<int16_t> audio_buffer_;

  /// Buffer for incoming TTS audio (raw 16-bit PCM, 48 kHz).
  std::vector<uint8_t> tts_buffer_;
  size_t tts_play_offset_{0};

  /// Timestamps for timeout detection.
  uint32_t last_activity_ms_{0};
  uint32_t stream_start_ms_{0};

  /// Maximum utterance length in ms (default 30s).
  uint32_t max_utterance_ms_{30000};

  /// Silence timeout in ms (stop streaming after this much silence).
  uint32_t silence_timeout_ms_{2000};
  uint32_t last_speech_ms_{0};
};

}  // namespace web_socket_voice
}  // namespace esphome
