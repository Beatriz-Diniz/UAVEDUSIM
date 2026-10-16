/****************************************************************************
 *
 *   UAVJamSim - Motor Attack Module
 *
 ****************************************************************************/

#pragma once

#include <cstdint>

class MotorAttack
{
public:
	// Atualiza o comando de ataque. Chamado pelo GZBridge (motorAttackCallback)
	// a partir do topico /gazebo/default/attack/motor.
	void set(int option, int index, double speed)
	{
		_motor_attack_option = option;
		_motor_attack_index = index;
		_motor_attack_speed = speed;
	}

	// Aplica o ataque no array de saidas dos motores, in-place.
	// E no-op se nenhuma opcao de ataque estiver ativa (option 0).
	void apply(uint16_t outputs[], unsigned active_output_count) const;

private:
	int _motor_attack_option{0};
	int _motor_attack_index{0};
	double _motor_attack_speed{0.0};
};