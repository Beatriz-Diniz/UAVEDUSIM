/****************************************************************************
 *
 *   UAVJamSim - GPS Absolute Offset Attack Module
 *
 ****************************************************************************/

#pragma once

#include <gz/math/Vector3.hh>
#include <gz/msgs.hh>
#include <gz/transport.hh>

class AbsoluteOffsetAttack
{
public:
	explicit AbsoluteOffsetAttack(gz::transport::Node &node) : _node(node) {}

	// Assina o topico de comando de ataque.
	bool init(const std::string &gps_attack_topic = "/gazebo/default/attack/gps");

	// Offsets atuais em graus (latitude/longitude) e metros (altitude).
	double offsetLatDeg() const { return _gps_attack_offset.X(); }
	double offsetLonDeg() const { return _gps_attack_offset.Y(); }
	double offsetAltM() const { return _gps_attack_offset.Z(); }

private:
	void gpsAttackCallback(const gz::msgs::Vector3d &msg);

	gz::transport::Node &_node;

	gz::math::Vector3d _gps_attack_offset{0.0, 0.0, 0.0};
};