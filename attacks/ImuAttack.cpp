/****************************************************************************
 *
 *   UAVJamSim - IMU Attack Module (implementation)
 *
 ****************************************************************************/

#include "ImuAttack.hpp"

#include <px4_platform_common/log.h>

bool ImuAttack::init(const std::string &imu_attack_topic)
{
	if (!_node.Subscribe(imu_attack_topic, &ImuAttack::imuAttackCallback, this)) {
		PX4_ERR("failed to subscribe to attack topic: %s", imu_attack_topic.c_str());
		return false;
	}

	return true;
}

// ======== ATAQUE IMU - INICIO ========
// Ataque na IMU: habilita offsets, desabilitacao total ou limpeza das perturbacoes por eixo.
void ImuAttack::imuAttackCallback(const gz::msgs::Vector3d &msg)
{
	int option = static_cast<int>(msg.x());
	int index = static_cast<int>(msg.y());
	double offset = msg.z();

	// Indices validos: 0..2 para acelerometro e 3..5 para giroscopio.
	if (index < 0 || index > 5) return;

	// Opcoes: 1 habilita offsets, 2 zera a IMU, 3 configura offset, 4 limpa, 5 desativa.
	switch (option) {
		case 1:
			_imu_attack_enabled = true;
			_imu_disabled = false;
			for (int i = 0; i < 6; i++) {
				_imu_active_offsets[i] = _imu_temp_offsets[i];
			}
			break;
		case 2:
			_imu_disabled = true;
			_imu_attack_enabled = false;
			break;
		case 3:
			_imu_temp_offsets[index] = offset;
			break;
		case 4:
			for (int i = 0; i < 6; i++) {
				_imu_temp_offsets[i] = 0.0;
				_imu_active_offsets[i] = 0.0;
			}
			break;
		case 5:
			_imu_attack_enabled = false;
			_imu_disabled = false;
			break;
	}
}
// ======== ATAQUE IMU - FIM ========

void ImuAttack::apply(float &accel_x, float &accel_y, float &accel_z,
		       float &gyro_x, float &gyro_y, float &gyro_z) const
{
	// ======== ATAQUE IMU - INICIO ========
	// Ataque IMU: pode zerar completamente o sensor ou somar offsets configurados por eixo.
	if (_imu_disabled) {
		accel_x = 0.0f; accel_y = 0.0f; accel_z = 0.0f;
		gyro_x = 0.0f; gyro_y = 0.0f; gyro_z = 0.0f;
	} else if (_imu_attack_enabled) {

		accel_x += static_cast<float>(_imu_active_offsets[0]);
		accel_y += static_cast<float>(_imu_active_offsets[1]);
		accel_z += static_cast<float>(_imu_active_offsets[2]);
		gyro_x += static_cast<float>(_imu_active_offsets[3]);
		gyro_y += static_cast<float>(_imu_active_offsets[4]);
		gyro_z += static_cast<float>(_imu_active_offsets[5]);
	}
	// ======== ATAQUE IMU - FIM ========
}