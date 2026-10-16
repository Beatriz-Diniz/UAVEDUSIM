/****************************************************************************
 *
 *   UAVJamSim - Motor Attack Module (implementation)
 *
 ****************************************************************************/

#include "MotorAttack.hpp"

// ====== INJEÇÃO DO ATAQUE DE MOTORES UAVJAMSIM ======
void MotorAttack::apply(uint16_t outputs[], unsigned active_output_count) const
{
	if (_motor_attack_option == 2) {
		// Desliga todos os motores zerando o array de saída
		for (unsigned i = 0; i < active_output_count; i++) {
			outputs[i] = 0.0f;
		}
	} else if (_motor_attack_option == 1) {
		// Sobrescreve apenas o motor selecionado com a velocidade forçada (0 a 1450 rad/s)
		if (_motor_attack_index >= 0 && (unsigned)_motor_attack_index < active_output_count) {
			outputs[_motor_attack_index] = static_cast<float>(_motor_attack_speed);
		}
	}
}
// ==================================================