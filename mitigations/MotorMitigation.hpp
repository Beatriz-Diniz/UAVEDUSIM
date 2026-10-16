/****************************************************************************
 *
 *   UAVJamSim - Motor Mitigation Module
 *
 *   Cobre deteccao (implausibilidade fisica do comando de motor) e
 *   pouso/desarme. Chamada de dois lugares diferentes: detectAndCorrect()
 *   a partir de GZMixingInterfaceESC::updateOutputs (work queue propria do
 *   motor), checkAutoLand/checkAutoDisarm a partir do sweep de pouso do
 *   GZBridge (work queue diferente) - por isso o estado de anomalia
 *   continua atomico, para leitura/escrita seguras entre as duas threads.
 *
 ****************************************************************************/

#pragma once

#include <atomic>
#include <cstdint>

#include <lib/geo/geo.h>

#include <uORB/Publication.hpp>
#include <uORB/Subscription.hpp>
#include <uORB/topics/actuator_armed.h>
#include <uORB/topics/position_setpoint_triplet.h>
#include <uORB/topics/vehicle_command.h>
#include <uORB/topics/vehicle_land_detected.h>

class MotorMitigation
{
public:
	void reset();

	// Liga/desliga a deteccao. Reinicia o estado de anomalia ao alternar.
	void setEnabled(bool enabled);
	bool isEnabled() const { return _motor_mitigation_enabled; }

	// Chamado de GZMixingInterfaceESC::updateOutputs a cada ciclo de saida.
	// Detecta implausibilidade fisica do comando (todos os motores perto de
	// zero com o veiculo armado e voando, ou um motor com saida muito
	// diferente da media dos demais) e restaura outputs[] a partir de
	// legitimate_outputs[] em-lugar (in-place) quando confirmado.
	void detectAndCorrect(uint16_t outputs[], const uint16_t legitimate_outputs[], unsigned active_output_count);

	bool isAnomalyActive() const { return _motor_anomaly_active.load(std::memory_order_relaxed); }

	// Pouso proprio de motor: usa LPOS como estimativa de posicao.
	// anomaly_active vem de isAnomalyActive() (chamado internamente pelo GZBridge).
	void checkAutoLand(uint64_t timestamp, MapProjection &pos_ref, bool anomaly_active,
			    bool lpos_xy_valid, uint64_t lpos_timestamp,
			    double lpos_n_m, double lpos_e_m, float lpos_ground_speed);

	// Confirma contato com o solo por multiplas fontes antes de desarmar.
	void checkAutoDisarm(uint64_t timestamp,
			      bool ground_distance_valid, uint64_t ground_distance_timestamp, float ground_distance_m,
			      bool sim_agl_valid, uint64_t sim_agl_timestamp, double sim_agl_m,
			      bool sim_att_valid, uint64_t sim_att_timestamp, float sim_roll_rad, float sim_pitch_rad,
			      float lpos_ground_speed);

	bool isLandingActive() const { return _motor_landing_active; }
	bool isAutoLandSent() const { return _motor_auto_land_sent; }
	bool isAutoDisarmSent() const { return _motor_auto_disarm_sent; }

private:
	void publishLandCommand(uint64_t timestamp, MapProjection &pos_ref);
	void publishDisarmCommand(uint64_t timestamp, bool force_disarm);

	// ======== Estado da deteccao ========
	// Nao atomico: so lido/escrito dentro de detectAndCorrect(), sempre na
	// mesma work queue (GZMixingInterfaceESC).
	bool _motor_mitigation_enabled{false};

	// Atomico: escrito em detectAndCorrect() (work queue do motor), lido em
	// isAnomalyActive() a partir do sweep de pouso do GZBridge (work queue
	// diferente).
	std::atomic<bool> _motor_anomaly_active{false};
	uint64_t _motor_anomaly_start_us{0};
	uint64_t _motor_recovery_start_us{0};

	// Uso exclusivo de detectAndCorrect() - subscricao propria, independente
	// da usada em checkAutoDisarm() (mesmo topico, duas subscricoes porque
	// sao chamadas de work queues diferentes).
	uORB::Subscription _actuator_armed_detect_sub{ORB_ID(actuator_armed)};
	uORB::Subscription _vehicle_land_detected_detect_sub{ORB_ID(vehicle_land_detected)};

	// Abaixo deste valor de saida (0-1450), o motor e considerado "sem empuxo relevante".
	static constexpr float MOTOR_MIN_ARMED_OUTPUT = 50.0f;
	// Desvio maximo tolerado entre a saida de um motor e a media dos demais,
	// como fracao da propria media.
	static constexpr float MOTOR_ASYMMETRY_FRACTION = 0.20f; // 20%
	// Piso absoluto para a media nao gerar um limiar irrisorio perto do solo.
	static constexpr float MOTOR_ASYMMETRY_MIN_ABS = 60.0f;
	// Tempo minimo sustentado para confirmar a anomalia/recuperacao (o
	// bloqueio do comando em si e imediato, sem essa espera).
	static constexpr uint64_t MOTOR_ANOMALY_CONFIRM_US = 100000ULL;  // 100 ms
	static constexpr uint64_t MOTOR_RECOVERY_CONFIRM_US = 300000ULL; // 300 ms

	// ======== Estado de pouso/desarme ========
	bool     _motor_landing_active{false};
	bool     _motor_auto_land_sent{false};
	bool     _motor_auto_disarm_sent{false};
	uint64_t _motor_auto_land_arrival_us{0};
	uint64_t _motor_auto_land_last_arrived_us{0};
	double   _motor_land_hold_n_m{0.0};
	double   _motor_land_hold_e_m{0.0};
	uint32_t _motor_sim_agl_ground_count{0};

	// ======== uORB proprios (nao compartilhados com o GZBridge) ========
	uORB::Subscription _pos_sp_triplet_sub{ORB_ID(position_setpoint_triplet)};
	uORB::Subscription _vehicle_land_detected_sub{ORB_ID(vehicle_land_detected)};
	uORB::Publication<vehicle_command_s> _vehicle_command_pub{ORB_ID(vehicle_command)};

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