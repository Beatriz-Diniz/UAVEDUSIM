/****************************************************************************
 *
 *   UAVJamSim - GPS Jamming Attack Module (implementation)
 *
 ****************************************************************************/

#include "JammingAttack.hpp"

#include <cmath>
#include <cstdlib>

#include <lib/geo/geo.h>
#include <lib/mathlib/mathlib.h>
#include <px4_platform_common/log.h>

bool JammingAttack::init(const std::string &jamming_attack_topic)
{
	if (!_node.Subscribe(jamming_attack_topic, &JammingAttack::jammingAttackCallback, this)) {
		PX4_ERR("failed to subscribe to attack topic: %s", jamming_attack_topic.c_str());
		return false;
	}

	return true;
}

// Gera uma amostra de ruido branco gaussiano com media zero e desvio padrao unitario.
float JammingAttack::generateWgn()
{
	// Implementacao Box-Muller polar com reaproveitamento alternado das amostras.
	static float V1, V2, S;
	static bool phase = true;
	float X;

	if (phase) {
		do {
			float U1 = (float)rand() / (float)RAND_MAX;
			float U2 = (float)rand() / (float)RAND_MAX;
			V1 = 2.0f * U1 - 1.0f;
			V2 = 2.0f * U2 - 1.0f;
			S = V1 * V1 + V2 * V2;
		} while (S >= 1.0f || fabsf(S) < 1e-8f);

		X = V1 * float(sqrtf(-2.0f * float(logf(S)) / S));

	} else {
		X = V2 * float(sqrtf(-2.0f * float(logf(S)) / S));
	}

	phase = !phase;
	return X;
}

// ======== ATAQUE GPS JAMMING - INICIO ========
// Ataque de jamming GPS: seleciona ruido continuo, blackout ou jamming pulsado.
void JammingAttack::jammingAttackCallback(const gz::msgs::Vector3d &msg)
{
	// msg.x seleciona o tipo de jamming e msg.y define a intensidade.
	_jamming_attack_type = static_cast<int>(msg.x());
	_jamming_intensity   = static_cast<float>(msg.y());

	// ======== ATAQUE GPS JAMMING DESATIVADO - INICIO ========
	// Tipo 0: desativa o jamming e reinicia a maquina de estados pulsada.
	if (_jamming_attack_type == 0) {

		_pjam_state                  = PulsedJamState::IDLE;
		_pjam_state_enter_us         = 0;
		_jamming_pulsed_initialized  = false;
		_jamming_intermittent_active = false;
		_pjam_noise_factor           = 0.0f;
		PX4_INFO("[Jamming] DISABLED");
	// ======== ATAQUE GPS JAMMING DESATIVADO - FIM ========

	// ======== ATAQUE 1 GPS NOISE JAMMING - INICIO ========
	// Tipo 1: jamming por ruido continuo aplicado a posicao, altitude e velocidade GPS.
	} else if (_jamming_attack_type == 1) {
		PX4_WARN("[Jamming] NOISE: ENABLED, Intensity=%.2f",
			 static_cast<double>(_jamming_intensity));
	// ======== ATAQUE 1 GPS NOISE JAMMING - FIM ========

	// ======== ATAQUE 2 GPS BLACKOUT - INICIO ========
	// Tipo 2: blackout, simulando perda total de atualizacao GPS.
	} else if (_jamming_attack_type == 2) {
		PX4_WARN("[Jamming] BLACKOUT: ENABLED (Signal Drop)");
	// ======== ATAQUE 2 GPS BLACKOUT - FIM ========

	// ======== ATAQUE 3 GPS PULSED JAMMING - INICIO ========
	// Tipo 3: jamming pulsado com subida, bloqueio, decaimento e recuperacao.
	} else if (_jamming_attack_type == 3) {

		// A intensidade controla a duracao do bloqueio total dentro do ciclo pulsado.
		float clamped = math::constrain(_jamming_intensity, 0.1f, 1.0f);

		_pjam_ramp_us  = 500000ULL;
		_pjam_on_us    = static_cast<uint64_t>(clamped * 10.0f * 1e6f);
		_pjam_decay_us = 800000ULL;
		_pjam_off_us   = 2000000ULL;

		if (!_jamming_pulsed_initialized) {
			_pjam_state                  = PulsedJamState::RAMP_UP;
			_pjam_state_enter_us         = hrt_absolute_time();
			_jamming_pulsed_initialized  = true;
			_jamming_intermittent_active = false;
			_pjam_noise_factor           = 0.0f;
		}

		PX4_WARN("[Jamming] PULSED ENABLED | Intensity=%.2f | ramp=%llums on=%llums decay=%llums off=%llums",
			 static_cast<double>(_jamming_intensity),
			 (unsigned long long)(_pjam_ramp_us  / 1000),
			 (unsigned long long)(_pjam_on_us    / 1000),
			 (unsigned long long)(_pjam_decay_us / 1000),
			 (unsigned long long)(_pjam_off_us   / 1000));
	}
	// ======== ATAQUE 3 GPS PULSED JAMMING - FIM ========
}
// ======== ATAQUE GPS JAMMING - FIM ========

// ======== ATAQUE 1 GPS NOISE JAMMING - INICIO ========
// Ataque 1: jamming por ruido continuo. A perturbacao afeta posicao, altitude e velocidade.
void JammingAttack::applyNoiseJamming(double &latitude, double &longitude, double &altitude,
				       float &vel_north, float &vel_east, float &vel_down) const
{
	if (_jamming_attack_type != 1) {
		return;
	}

	// Ruido horizontal em metros convertido para latitude/longitude.
	const double lat_rad = math::radians(latitude);
	const double noise_n_m = static_cast<double>(generateWgn() * _jamming_intensity * 10.0f);
	const double noise_e_m = static_cast<double>(generateWgn() * _jamming_intensity * 10.0f);

	latitude  += math::degrees(noise_n_m / CONSTANTS_RADIUS_OF_EARTH);
	longitude += math::degrees(noise_e_m / (CONSTANTS_RADIUS_OF_EARTH * cos(lat_rad)));

	// O canal vertical recebe ruido maior para representar a menor precisao vertical do GPS.
	altitude += static_cast<double>(generateWgn() * _jamming_intensity * 30.0f);

	// Velocidades tambem sao perturbadas para simular uma medicao GPS dinamicamente inconsistente.
	vel_north += generateWgn() * _jamming_intensity * 10.0f;
	vel_east  += generateWgn() * _jamming_intensity * 10.0f;
	vel_down  += generateWgn() * _jamming_intensity * 10.0f;
}
// ======== ATAQUE 1 GPS NOISE JAMMING - FIM ========

// ======== ATAQUE 3 GPS PULSED JAMMING - INICIO ========
// Ataque 3: maquina de estados do jamming pulsado.
void JammingAttack::tickPulsedJamming(uint64_t now_us, double &latitude, double &longitude, double &altitude,
				       float &vel_north, float &vel_east, float &vel_down)
{
	if (_jamming_attack_type != 3 || !_jamming_pulsed_initialized) {
		return;
	}

	const uint64_t elapsed = now_us - _pjam_state_enter_us;

	// Estados: subida do jammer, bloqueio total, decaimento e periodo valido.
	switch (_pjam_state) {

	case PulsedJamState::RAMP_UP: {
		// RAMP_UP: o sinal piora gradualmente e o ruido cresce de 0 ate 100%.

		_pjam_noise_factor = math::constrain(
					     static_cast<float>(elapsed) / static_cast<float>(_pjam_ramp_us),
					     0.0f, 1.0f);
		_jamming_intermittent_active = true;

		if (elapsed >= _pjam_ramp_us) {
			_pjam_state          = PulsedJamState::JAM;
			_pjam_state_enter_us = now_us;
			_pjam_noise_factor   = 1.0f;
			PX4_WARN("[UAVJamSim Jamming] >>> JAM ON <<< (%.1f s)",
				 static_cast<double>(_pjam_on_us) / 1e6);
		}

		break;
	}

	case PulsedJamState::JAM: {
		// JAM: bloqueio total; nenhuma mensagem GPS e publicada enquanto o pulso esta ativo.
		// A supressao da publicacao em si e feita pelo chamador, consultando isPulsedFullyJammed().
		_pjam_noise_factor            = 1.0f;
		_jamming_intermittent_active  = true;

		if (elapsed >= _pjam_on_us) {
			_pjam_state          = PulsedJamState::DECAY;
			_pjam_state_enter_us = now_us;
			PX4_INFO("[UAVJamSim Jamming] <<< JAM OFF — DECAY >>>");
		}

		break;
	}

	case PulsedJamState::DECAY: {
		// DECAY: o jammer deixa de bloquear e o ruido reduz gradualmente.

		_pjam_noise_factor = math::constrain(
					     1.0f - static_cast<float>(elapsed) / static_cast<float>(_pjam_decay_us),
					     0.0f, 1.0f);
		_jamming_intermittent_active = (_pjam_noise_factor > 0.05f);

		if (elapsed >= _pjam_decay_us) {
			_pjam_state                  = PulsedJamState::VALID;
			_pjam_state_enter_us         = now_us;
			_pjam_noise_factor           = 0.0f;
			_jamming_intermittent_active = false;
			PX4_INFO("[UAVJamSim Jamming] <<< GPS VALID — recovery >>>");
		}
		break;
	}

	case PulsedJamState::VALID: {
		// VALID: janela com GPS limpo antes do proximo pulso.
		_pjam_noise_factor            = 0.0f;
		_jamming_intermittent_active  = false;

		if (elapsed >= _pjam_off_us) {

			_pjam_state          = PulsedJamState::RAMP_UP;
			_pjam_state_enter_us = now_us;
			PX4_WARN("[UAVJamSim Jamming] >>> NEW PULSE — RAMP_UP >>>");
		}
		break;
	}

	default:
		break;
	}

	// Em RAMP_UP/DECAY, injeta ruido proporcional a potencia instantanea do pulso.
	if (_pjam_noise_factor > 0.0f) {
		const float noise_factor = _pjam_noise_factor;
		const double lat_rad = math::radians(latitude);
		const double noise_n_m = static_cast<double>(generateWgn() * noise_factor * _jamming_intensity * 1000.0f);
		const double noise_e_m = static_cast<double>(generateWgn() * noise_factor * _jamming_intensity * 1000.0f);

		latitude  += math::degrees(noise_n_m / CONSTANTS_RADIUS_OF_EARTH);
		longitude += math::degrees(noise_e_m / (CONSTANTS_RADIUS_OF_EARTH * cos(lat_rad)));
		altitude  += static_cast<double>(generateWgn() * noise_factor * _jamming_intensity * 80.0f);
		vel_north += generateWgn() * noise_factor * _jamming_intensity * 15.0f;
		vel_east  += generateWgn() * noise_factor * _jamming_intensity * 15.0f;
		vel_down  += generateWgn() * noise_factor * _jamming_intensity * 15.0f;
	}
}
// ======== ATAQUE 3 GPS PULSED JAMMING - FIM ========

// Degrada eph/epv/hdop/vdop/satellites_used de acordo com o ataque de jamming ativo.
void JammingAttack::degradeGpsQuality(sensor_gps_s &sensor_gps, int nominal_satellites) const
{
	// ======== ATAQUE 3 GPS PULSED JAMMING - INICIO ========
	// No jamming pulsado, degrada eph/epv/HDOP/VDOP e reduz satelites durante RAMP_UP/DECAY.
	if (_pjam_noise_factor > 0.0f) {
		const float noise_factor = _pjam_noise_factor;

		sensor_gps.eph  = sensor_gps.eph  + noise_factor * (15.0f - sensor_gps.eph);
		sensor_gps.epv  = sensor_gps.epv  + noise_factor * (25.0f - sensor_gps.epv);
		sensor_gps.hdop = sensor_gps.hdop + noise_factor * (8.0f  - sensor_gps.hdop);
		sensor_gps.vdop = sensor_gps.vdop + noise_factor * (12.0f - sensor_gps.vdop);

		sensor_gps.satellites_used = static_cast<uint8_t>(
			math::constrain(static_cast<int>(nominal_satellites - static_cast<int>(noise_factor * (nominal_satellites - 3))),
					 3, nominal_satellites));
	} else {
		sensor_gps.satellites_used = static_cast<uint8_t>(nominal_satellites);
	}
	// ======== ATAQUE 3 GPS PULSED JAMMING - FIM ========

	// ======== QUALIDADE GPS DURANTE NOISE JAMMING - INICIO ========
	// O GPS ruidoso continua valido, mas com incerteza maior para evitar
	// que o estimador trate saltos grandes como medicoes precisas.
	if (_jamming_attack_type == 1) {
		sensor_gps.eph  = math::max(sensor_gps.eph,  8.0f);
		sensor_gps.epv  = math::max(sensor_gps.epv,  12.0f);
		sensor_gps.hdop = math::max(sensor_gps.hdop, 2.5f);
		sensor_gps.vdop = math::max(sensor_gps.vdop, 3.5f);
	}
	// ======== QUALIDADE GPS DURANTE NOISE JAMMING - FIM ========
}