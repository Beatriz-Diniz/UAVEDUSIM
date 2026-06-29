#!/usr/bin/env bash

echo ""
echo "[cleanup] Stopping PX4/Gazebo processes..."

pkill -INT  -f "bin/px4" 2>/dev/null || true
pkill -INT  -f "gz sim" 2>/dev/null || true
pkill -INT  -f "gzserver" 2>/dev/null || true
pkill -INT  -f "gzclient" 2>/dev/null || true
pkill -INT  -f "parameter_bridge" 2>/dev/null || true

sleep 2

pkill -TERM -f "bin/px4" 2>/dev/null || true
pkill -TERM -f "gz sim" 2>/dev/null || true
pkill -TERM -f "gzserver" 2>/dev/null || true
pkill -TERM -f "gzclient" 2>/dev/null || true
pkill -TERM -f "parameter_bridge" 2>/dev/null || true

sleep 1

pkill -KILL -f "bin/px4" 2>/dev/null || true
pkill -KILL -f "gz sim" 2>/dev/null || true
pkill -KILL -f "gzserver" 2>/dev/null || true
pkill -KILL -f "gzclient" 2>/dev/null || true
pkill -KILL -f "parameter_bridge" 2>/dev/null || true

echo "[cleanup] Done."
