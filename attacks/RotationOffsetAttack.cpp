/****************************************************************************
 *
 *   UAVJamSim - GPS Rotation Offset Attack Module (implementation)
 *
 ****************************************************************************/

#include "RotationOffsetAttack.hpp"

#include <px4_platform_common/log.h>

bool RotationOffsetAttack::init(const std::string &gps_rot_attack_topic)
{
	if (!_node.Subscribe(gps_rot_attack_topic, &RotationOffsetAttack::gpsRotAttackCallback, this)) {
		PX4_ERR("failed to subscribe to attack topic: %s", gps_rot_attack_topic.c_str());
		return false;
	}

	return true;
}

// ======== ATAQUE GPS ROTACAO - INICIO ========
// Ataque GPS por rotacao: configura magnitude, angulo e offset vertical da perturbacao circular.
void RotationOffsetAttack::gpsRotAttackCallback(const gz::msgs::Vector3d &msg)
{

	// msg.x define o raio em graus, msg.y o angulo e msg.z o deslocamento vertical.
	_gps_attack_rot.Set(msg.x(), msg.y(), msg.z());
}
// ======== ATAQUE GPS ROTACAO - FIM ========