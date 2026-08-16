#pragma once

#include "esphome/core/component.h"
#include "esphome/core/helpers.h"
#include "esphome/core/log.h"
#include "esphome/components/microphone/microphone.h"
#include "esphome/components/speaker/speaker.h"
#include "esphome/components/script/script.h"
#include "esphome/components/globals/globals_component.h"

#include "esp_websocket_client.h"
#include "cJSON.h"

// Explicit includes for std namespace types — ESP-IDF 14.2.0 toolchain
// may not pull these transitively through ESPHome headers, causing
// "'vector' in namespace 'esphome::web_socket_voice::std'" errors.
#include <vector>
#include <string>

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
  void set_server_host(const ::std::string &host) { host_ = host; }
  void set_server_port(uint16_t port) { port_ = port; }

  /// Set pointer to the stock voice_assistant_phase global (for LED control).
  void set_voice_assistant_phase(globals::GlobalsComponent<int> *phase) {
    voice_assistant_phase_ = phase;
  }
  /// Set pointer to the stock control_leds script (for LED animations).
  void set_control_leds(script::SingleScript<> *script) {
    control_leds_ = script;
  }

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

  /// Get current state (for debug logging from YAML lambdas).
  uint8_t get_state() const { return static_cast<uint8_t>(state_); }

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
  void on_mic_data_(const ::std::vector<uint8_t> &data);

  // ── State management ───────────────────────────────────────────────
  void set_state_(VoiceState new_state);

  ::std::string host_;
  uint16_t port_{8765};

  VoiceState state_{VoiceState::IDLE};
  esp_websocket_client_handle_t ws_client_{nullptr};

  microphone::Microphone *mic_{nullptr};
  speaker::Speaker *spk_{nullptr};

  /// Pointers to stock package globals for LED control (set via YAML wiring).
  globals::GlobalsComponent<int> *voice_assistant_phase_{nullptr};
  script::SingleScript<> *control_leds_{nullptr};

  /// Total mic bytes received during current utterance (for silence threshold).
  uint32_t total_mic_bytes_{0};

  /// Pre-connect audio buffer: mic data received before the WebSocket
  /// handshake completes. Once connected, this buffer is flushed and cleared.
  ::std::vector<uint8_t> preconnect_buffer_;

  /// Buffer for incoming TTS audio (raw PCM).
  ::std::vector<uint8_t> tts_buffer_;
  size_t tts_play_offset_{0};

  /// Timestamps for timeout detection (ms).
  uint32_t stream_start_ms_{0};
  uint32_t last_speech_ms_{0};
  /// Timestamp of the last WebSocket connection attempt (ms)
  /// — used in conjunction with RECONNECT_DELAY_MS to throttle
  /// reconnection frequency.
  uint32_t last_connect_ms_{0};

  /// Maximum utterance length (default 10s).
  uint32_t max_utterance_ms_{10000};
  /// Silence timeout (stop after this much silence).
  uint32_t silence_timeout_ms_{1500};

  /// Flag: has the mic data callback already been registered?
  bool mic_callback_added_{false};

  /// Flag: WebSocket disconnected while in websocket task; main loop
  /// must destroy the handle (don't call destroy/stop from event handler).
  bool ws_needs_reconnect_{false};

  /// Flag: has utterance_start been sent for this utterance?
  /// Prevents duplicate sends — set true on first mic data chunk
  /// or on WEBSOCKET_EVENT_CONNECTED if already streaming.
  bool utterance_start_sent_{false};
};

}  // namespace web_socket_voice
}  // namespace esphome
