/****************************************************************************
 *
 *   UAVJamSim - LiDAR Distance Attack Module (implementation)
 *
 ****************************************************************************/

#include "LidarAttack.hpp"

#include <px4_platform_common/log.h>

bool LidarAttack::init(const std::string &lidar_attack_topic)
{
	if (!_node.Subscribe(lidar_attack_topic, &LidarAttack::lidarAttackCallback, this)) {
		PX4_ERR("failed to subscribe to attack topic: %s", lidar_attack_topic.c_str());
		return false;
	}

	return true;
}

// ======== ATAQUE LIDAR - INICIO ========
// Ataque no LiDAR: configura ativacao e offset de distancia em metros.
void LidarAttack::lidarAttackCallback(const gz::msgs::Vector3d &msg)
{

	// msg.x habilita/desabilita o ataque; msg.y define o deslocamento de distancia.
	_lidar_attack_option = static_cast<int>(msg.x());
	_lidar_distance_offset = msg.y();

	PX4_INFO("Lidar Attack: Option=%d, Offset=%.2f", _lidar_attack_option, _lidar_distance_offset);
}
// ======== ATAQUE LIDAR - FIM ========

// Ataque LiDAR (sensores 1D, down/front): soma offset a medicao de distancia.
float LidarAttack::applyOffset(float distance) const
{
	if (_lidar_attack_option == 1) {
		distance += static_cast<float>(_lidar_distance_offset);
	}

	return distance;
}

// Ataque LiDAR 2D: reaproveita o mesmo comando/estado do ataque do LiDAR 1D
// (--lidar 1 <offset>): soma o offset a cada feixe valido antes da reducao
// em setores.
double LidarAttack::applyOffset(double distance) const
{
	if (_lidar_attack_option == 1) {
		distance += _lidar_distance_offset;
	}

	return distance;
}