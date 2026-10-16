/****************************************************************************
 *
 *   UAVJamSim - GPS Jamming Attack Module
 *
 ****************************************************************************/

#pragma once

#include <cstdint>

#include <gz/msgs.hh>
#include <gz/transport.hh>

#include <uORB/topics/sensor_gps.h>

class JammingAttack
{
public:
	explicit JammingAttack(gz::transport::Node &node) : _node(node) {}

	// Assina o topico de comando de ataque.
	bool init(const std::string &jamming_attack_topic = "/gazebo/default/attack/jamming");

	// ======== Estado / consulta ========
	int   type() const { return _jamming_attack_type; }        // 0=OFF, 1=ruido, 2=blackout, 3=pulsado
	float intensity() const { return _jamming_intensity; }
	bool  isOff() const { return _jamming_attack_type == 0; }
	bool  isBlackoutActive() const { return _jamming_attack_type == 2; }
	bool  isNoiseActive() const { return _jamming_attack_type == 1; }
	bool  isPulsedActive() const { return _jamming_attack_type == 3; }

	// true enquanto o pulso esta em RAMP_UP/JAM/DECAY.
	bool  isPulsedIntermittentActive() const { return _jamming_intermittent_active; }
	// true durante o estado JAM do pulso (bloqueio total do sinal).
	bool  isPulsedFullyJammed() const { return _pjam_state == PulsedJamState::JAM; }
	float pulsedNoiseFactor() const { return _pjam_noise_factor; }

	// ======== Injecao de ataque ========

	// Ataque 1 (ruido continuo). So aplica se type()==1; caso contrario e no-op.
	void applyNoiseJamming(double &latitude, double &longitude, double &altitude,
			       float &vel_north, float &vel_east, float &vel_down) const;

	// Ataque 3 (pulsado): avanca a maquina de estados e injeta ruido proporcional
	// a potencia instantanea do pulso. So aplica se type()==3; caso contrario e no-op.
	// now_us deve ser hrt_absolute_time() do chamador.
	void tickPulsedJamming(uint64_t now_us, double &latitude, double &longitude, double &altitude,
				float &vel_north, float &vel_east, float &vel_down);

	// Degrada eph/epv/hdop/vdop/satellites_used de acordo com o ataque ativo
	// (pulsado ou ruido continuo). Chamar depois de preencher os valores
	// nominais de sensor_gps, antes de publicar.
	void degradeGpsQuality(sensor_gps_s &sensor_gps, int nominal_satellites) const;

private:
	void jammingAttackCallback(const gz::msgs::Vector3d &msg);

	// Gera uma amostra de ruido branco gaussiano com media zero e desvio padrao unitario.
	static float generateWgn();

	gz::transport::Node &_node;

	int   _jamming_attack_type{0};
	float _jamming_intensity{0.0f};

	enum class PulsedJamState {
		IDLE = 0,
		RAMP_UP,
		JAM,
		DECAY,
		VALID
	};

	PulsedJamState _pjam_state{PulsedJamState::IDLE};

	uint64_t _pjam_state_enter_us{0};
	uint64_t _pjam_ramp_us{500000ULL};
	uint64_t _pjam_on_us{1000000ULL};
	uint64_t _pjam_decay_us{800000ULL};
	uint64_t _pjam_off_us{2000000ULL};

	float _pjam_noise_factor{0.0f};

	bool _jamming_intermittent_active{false};
	bool _jamming_pulsed_initialized{false};
};