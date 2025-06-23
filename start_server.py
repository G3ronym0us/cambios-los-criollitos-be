#!/usr/bin/env python3
"""
Script para iniciar el servidor de tasas de cambio
"""

import uvicorn
import sys
import os

def main():
    print("=" * 60)
    print("🏦 SISTEMA DE TASAS DE CAMBIO")
    print("=" * 60)
    print("🚀 Iniciando servidor...")
    print("📍 URL Principal:      http://localhost:8000")
    print("📚 Documentación:      http://localhost:8000/docs")
    print("💚 Health Check:       http://localhost:8000/api/health")
    print("👥 Usuarios:           http://localhost:8000/api/users")
    print("💱 Tasas:              http://localhost:8000/api/rates")
    print("🔄 Conversión:         http://localhost:8000/api/convert")
    print("=" * 60)
    print()
    
    # Ejecutar el servidor
    try:
        uvicorn.run(
            "app.main:app",
            host="0.0.0.0",
            port=8000,
            reload=True,
            log_level="info"
        )
    except KeyboardInterrupt:
        print("\n🛑 Servidor detenido por el usuario")
    except Exception as e:
        print(f"❌ Error al iniciar servidor: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()