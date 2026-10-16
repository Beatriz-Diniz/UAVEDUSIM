/****************************************************************************
 *
 *   UAVJamSim - LiDAR Distance Attack Module
 *
 ****************************************************************************/

#pragma once

#include <gz/msgs.hh>
#include <gz/transport.hh>

class LidarAttack
{
public:
	explicit LidarAttack(gz::transport::Node &node) : _node(node) {}

	// Assina o topico de comando de ataque.
	bool init(const std::string &lidar_attack_topic = "/gazebo/default/attack/lidar");

	// Opcao ativa (0=desativado, 1=offset constante) e offset atual em metros.
	int option() const { return _lidar_attack_option; }
	double offsetM() const { return _lidar_distance_offset; }

	// Soma o offset a uma medicao de distancia (metros), usada pelos
	// sensores 1D (down/front) e por cada feixe do scan 2D antes da
	// reducao em setores. E no-op (retorna distance inalterada) se a
	// opcao ativa nao for 1.
	float applyOffset(float distance) const;
	double applyOffset(double distance) const;

private:
	void lidarAttackCallback(const gz::msgs::Vector3d &msg);

	gz::transport::Node &_node;

	int _lidar_attack_option{0};
	double _lidar_distance_offset{0.0};
};