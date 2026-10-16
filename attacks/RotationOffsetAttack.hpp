/****************************************************************************
 *
 *   UAVJamSim - GPS Rotation Offset Attack Module
 *
 ****************************************************************************/

#pragma once

#include <gz/math/Vector3.hh>
#include <gz/msgs.hh>
#include <gz/transport.hh>

class RotationOffsetAttack
{
public:
	explicit RotationOffsetAttack(gz::transport::Node &node) : _node(node) {}

	// Assina o topico de comando de ataque.
	bool init(const std::string &gps_rot_attack_topic = "/gazebo/default/attack/gps_rot");

	// Raio (graus), angulo (graus) e deslocamento vertical (metros) atuais.
	double radiusDeg() const { return _gps_attack_rot.X(); }
	double angleDeg() const { return _gps_attack_rot.Y(); }
	double offsetAltM() const { return _gps_attack_rot.Z(); }

private:
	void gpsRotAttackCallback(const gz::msgs::Vector3d &msg);

	gz::transport::Node &_node;

	gz::math::Vector3d _gps_attack_rot{0.0, 0.0, 0.0};
};