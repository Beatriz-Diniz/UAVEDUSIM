/****************************************************************************
 *
 *   UAVJamSim - GPS Absolute Offset Attack Module (implementation)
 *
 ****************************************************************************/

#include "AbsoluteOffsetAttack.hpp"

#include <px4_platform_common/log.h>

bool AbsoluteOffsetAttack::init(const std::string &gps_attack_topic)
{
	if (!_node.Subscribe(gps_attack_topic, &AbsoluteOffsetAttack::gpsAttackCallback, this)) {
		PX4_ERR("failed to subscribe to attack topic: %s", gps_attack_topic.c_str());
		return false;
	}

	return true;
}

// ======== ATAQUE GPS OFFSET - INICIO ========
// Ataque GPS por offset: atualiza deslocamentos artificiais de latitude, longitude e altitude.
void AbsoluteOffsetAttack::gpsAttackCallback(const gz::msgs::Vector3d &msg)
{

	// msg.x/y/z representam offsets aplicados diretamente a latitude, longitude e altitude.
	_gps_attack_offset.Set(msg.x(), msg.y(), msg.z());
}
// ======== ATAQUE GPS OFFSET - FIM ========