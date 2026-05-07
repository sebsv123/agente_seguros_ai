#!/usr/bin/env python3
"""
Crea recovered_workflows/agente_workflows_v2.json
- Cambia GPT-4.1-mini por DeepSeek V3 (deepseek-chat)
- Actualiza system prompt
- Mantiene JSON schema de salida
- Temperature: 0.2, Max tokens: 300
"""

import json
import os

with open('recovered_workflows/agente_workflows.json', 'r', encoding='utf-8') as f:
    wf = json.load(f)

# Find and modify the OpenAI node
for node in wf['nodes']:
    if node.get('type') == '@n8n/n8n-nodes-langchain.openAi':
        print(f"Modifying node: {node['name']}")

        # Change model to deepseek-chat
        node['parameters']['modelId'] = {
            '__rl': True,
            'value': 'deepseek-chat',
            'mode': 'list',
            'cachedResultName': 'DeepSeek V3'
        }

        # Update instructions
        node['parameters']['options']['instructions'] = (
            'Eres el asistente comercial de Valentín Protección Integral. '
            'Recibes leads de WhatsApp que vienen de Google Ads. '
            'Objetivo: clasificar interés, calificar al lead, obtener datos mínimos y derivar al equipo. '
            'NUNCA menciones marcas de aseguradoras (ASISA, Mapfre, Sanitas, Adeslas, etc.). '
            'Responde en español, sé claro y breve. '
            'Si faltan datos, haz 1-2 preguntas. '
            'No inventes coberturas ni precios.'
        )

        # Reduce max_tokens to 300
        node['parameters']['options']['maxTokens'] = 300

        # Keep temperature at 0.2
        node['parameters']['options']['temperature'] = 0.2

        # Add base URL for DeepSeek
        node['parameters']['options']['baseURL'] = 'https://api.deepseek.com/v1'

        # Update credential reference
        node['credentials'] = {
            'openAiApi': {
                'id': 'deepseek_credential',
                'name': 'DeepSeek API (OpenAI compatible)'
            }
        }

        print('  Model: gpt-4.1-mini -> deepseek-chat')
        print('  Max tokens: 150 -> 300')
        print('  Temperature: 0.2 (kept)')
        print('  Base URL added: https://api.deepseek.com/v1')
        print('  Instructions updated with Valentin Proteccion Integral context')
        print('  JSON schema: KEPT (same as original)')

# Update workflow name
wf['name'] = 'Agente de seguros 0.0.2 (DeepSeek V3)'

# Save v2
os.makedirs('recovered_workflows', exist_ok=True)
with open('recovered_workflows/agente_workflows_v2.json', 'w', encoding='utf-8') as f:
    json.dump(wf, f, ensure_ascii=False, indent=2)

print(f'\nSaved: recovered_workflows/agente_workflows_v2.json')
print(f'Workflow: {wf["name"]}')
print(f'Nodes: {len(wf["nodes"])}')
