/****************************************************************************
 *
 *   UAVJamSim - IMU Attack Module
 *
 ****************************************************************************/

#pragma once

#include <gz/msgs.hh>
#include <gz/transport.hh>

class ImuAttack
{
public:
	explicit ImuAttack(gz::transport::Node &node) : _node(node) {}

	// Assina o topico de comando de ataque.
	bool init(const std::string &imu_attack_topic = "/gazebo/default/attack/imu");

	// Aplica o ataque IMU: pode zerar completamente o sensor ou somar
	// offsets configurados por eixo. E no-op se nenhum ataque estiver
	// habilitado.
	void apply(float &accel_x, float &accel_y, float &accel_z,
		   float &gyro_x, float &gyro_y, float &gyro_z) const;

private:
	void imuAttackCallback(const gz::msgs::Vector3d &msg);

	gz::transport::Node &_node;

	bool _imu_attack_enabled{false};
	bool _imu_disabled{false};
	double _imu_temp_offsets[6]{0.0, 0.0, 0.0, 0.0, 0.0, 0.0};
	double _imu_active_offsets[6]{0.0, 0.0, 0.0, 0.0, 0.0, 0.0};
};