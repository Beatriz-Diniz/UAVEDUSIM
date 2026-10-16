/****************************************************************************
 *
 *   UAVJamSim - Barometer Mitigation Module
 *
 *   Detecta anomalias na altitude implicita na pressao publicada: divergencia
 *   contra o valor esperado por uma ancora entre essa altitude e a altitude
 *   do GPS (referencia independente), calibrada a partir das primeiras
 *   amostras saudaveis e adaptada lentamente depois. O ataque e um offset
 *   constante de altitude, que uma comparacao por taxa de variacao nunca
 *   pegaria (o offset se cancela na diferenca entre amostras): so uma
 *   comparacao por valor absoluto contra essa ancora detecta.
 *
 *   Quando confirmada, reconstroi a pressao a partir da altitude esperada
 *   (ancora + altitude atual do GPS), usando o modelo de atmosfera padrao.
 *   A temperatura publicada nunca precisa ser reconstruida: vem de um
 *   caminho (airspeedCallback) que o ataque de barometro nunca toca.
 *
 ****************************************************************************/

#pragma once

#include <cstdint>

#include <lib/geo/geo.h>

#include <uORB/Publication.hpp>
#include <uORB/Subscription.hpp>
#include <uORB/topics/position_setpoint_triplet.h>
#include <uORB/topics/vehicle_command.h>
#include <uORB/topics/vehicle_land_detected.h>

class BarometerMitigation
{
public:
	void reset();

	// Chamado de GZBridge::airPressureCallback apos o ataque ser aplicado.
	// gps_real_valid/gps_real_alt_timestamp/gps_real_alt sao a referencia
	// independente (cache de GPS, compartilhado com outras mitigacoes).
	// Corrige out_pressure/out_temperature em-lugar (in-place) quando a
	// anomalia esta confirmada.
	void detectAndCorrect(bool mitigation_enabled, uint64_t timestamp,
			      bool gps_real_valid, uint64_t gps_real_alt_timestamp, double gps_real_alt,
			      float raw_temperature,
			      float &out_pressure, float &out_temperature);

	// Pouso proprio de barometro: usa LPOS como estimativa de posicao.
	void checkAutoLand(uint64_t timestamp, MapProjection &pos_ref,
			    bool lpos_xy_valid, uint64_t lpos_timestamp,
			    double lpos_n_m, double lpos_e_m, float lpos_ground_speed);

	// Confirma contato com o solo por multiplas fontes antes de desarmar.
	void checkAutoDisarm(uint64_t timestamp,
			      bool ground_distance_valid, uint64_t ground_distance_timestamp, float ground_distance_m,
			      bool sim_agl_valid, uint64_t sim_agl_timestamp, double sim_agl_m,
			      bool sim_att_valid, uint64_t sim_att_timestamp, float sim_roll_rad, float sim_pitch_rad,
			      float lpos_ground_speed);

	bool isAnomalyActive() const { return _baro_anomaly_active; }
	bool isLandingActive() const { return _baro_landing_active; }
	bool isAutoLandSent() const { return _baro_auto_land_sent; }
	bool isAutoDisarmSent() const { return _baro_auto_disarm_sent; }

private:
	void publishLandCommand(uint64_t timestamp, MapProjection &pos_ref);
	void publishDisarmCommand(uint64_t timestamp, bool force_disarm);

	// ======== Linha de base adaptativa (ancora altitude-barometro vs GPS) ========
	bool     _baro_baseline_initialized{false};
	uint32_t _baro_baseline_warmup_count{0};
	double   _baro_anchor_offset_m{0.0}; // altitude_barometro - altitude_gps, na calibracao

	// ======== Estado da anomalia ========
	bool     _baro_anomaly_active{false};
	uint64_t _baro_anomaly_start_us{0};
	uint64_t _baro_recovery_start_us{0};
	uint32_t _baro_mitig_diag_count{0};

	// ======== Estado de pouso/desarme (proprio) ========
	bool     _baro_landing_active{false};
	bool     _baro_auto_land_sent{false};
	bool     _baro_auto_disarm_sent{false};
	uint64_t _baro_auto_land_arrival_us{0};
	uint64_t _baro_auto_land_last_arrived_us{0};
	double   _baro_land_hold_n_m{0.0};
	double   _baro_land_hold_e_m{0.0};
	uint32_t _baro_sim_agl_ground_count{0};

	// ======== uORB proprios (nao compartilhados com o GZBridge) ========
	uORB::Subscription _pos_sp_triplet_sub{ORB_ID(position_setpoint_triplet)};
	uORB::Subscription _vehicle_land_detected_sub{ORB_ID(vehicle_land_detected)};
	uORB::Publication<vehicle_command_s> _vehicle_command_pub{ORB_ID(vehicle_command)};

	// ======== Constantes de deteccao ========
	static constexpr uint32_t BARO_BASELINE_WARMUP_SAMPLES = 30;
	static constexpr double   BARO_BASELINE_WARMUP_ALPHA = 0.25;
	static constexpr double   BARO_BASELINE_ALPHA = 0.01;
	static constexpr uint32_t BARO_MITIG_DIAG_MAX_COUNT = 3;
	static constexpr double BARO_RESIDUAL_THRESHOLD_M = 3.0;
	static constexpr uint64_t BARO_ANOMALY_CONFIRM_US = 1500000ULL; // 1.5 s
	static constexpr uint64_t BARO_RECOVERY_CONFIRM_US = 500000ULL; // 500 ms
	static constexpr uint64_t BARO_GPS_ALT_MAX_AGE_US = 1000000ULL; // 1 s

	// ======== Constantes de pouso/desarme (mesmos valores do molde antigo) ========
	static constexpr float AUTO_LAND_ANOMALY_DEST_RADIUS_M = 1.2f;
	static constexpr float AUTO_LAND_ANOMALY_MAX_SPEED_M_S = 0.45f;
	static constexpr uint64_t AUTO_LAND_STABLE_US = 1500000ULL;
	static constexpr uint64_t AUTO_LAND_GROUND_SENSOR_TIMEOUT_US = 1000000ULL;
	static constexpr float AUTO_LAND_GROUND_DISARM_DIST_M = 0.18f;
	static constexpr double AUTO_LAND_SIM_GROUND_DISARM_AGL_M = 0.12;
	static constexpr float AUTO_LAND_MAX_PITCH_RAD = 0.0872665f;
	static constexpr float AUTO_LAND_MAX_ROLL_RAD = 0.0872665f;
	static constexpr uint32_t SIM_AGL_GROUND_CONFIRM_COUNT = 5;
};