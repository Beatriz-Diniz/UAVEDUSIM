/****************************************************************************
 *
 *   UAVJamSim - Magnetometer Attack Module (implementation)
 *
 ****************************************************************************/

#include "MagnetometerAttack.hpp"

#include <px4_platform_common/log.h>

bool MagnetometerAttack::init(const std::string &mag_attack_topic)
{
	if (!_node.Subscribe(mag_attack_topic, &MagnetometerAttack::magAttackCallback, this)) {
		PX4_ERR("failed to subscribe to attack topic: %s", mag_attack_topic.c_str());
		return false;
	}

	return true;
}

// ======== ATAQUE MAGNETOMETRO - INICIO ========
// Ataque no magnetometro: seleciona o modo de alteracao aplicado durante a publicacao do sensor.
void MagnetometerAttack::magAttackCallback(const gz::msgs::Vector3d &msg)
{

	// A opcao e aplicada posteriormente em magnetometerCallback().
	_mag_attack_option = static_cast<int>(msg.x());
}
// ======== ATAQUE MAGNETOMETRO - FIM ========

// Ataque de magnetometro: opcao 1 troca os eixos X e Y.
void MagnetometerAttack::apply(float raw_x, float raw_y, float raw_z,
				float &out_x, float &out_y, float &out_z) const
{
	switch (_mag_attack_option) {
		case 1:
		out_x = raw_y;
		out_y = raw_x;
		out_z = raw_z;

		break;

	default:
		out_x = raw_x;
		out_y = raw_y;
		out_z = raw_z;
		break;
	}
}