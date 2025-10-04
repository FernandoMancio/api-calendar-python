import os
import json
import psycopg2
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from google.oauth2 import service_account
from googleapiclient.discovery import build

# Inicializa a aplicação Flask
app = Flask(__name__)

# --- CARREGANDO AS CREDENCIAIS DE FORMA SEGURA ---
# O Render vai nos fornecer as credenciais como variáveis de ambiente.
# Este bloco de código as lê e as monta no formato que a biblioteca do Google espera.
try:
    SERVICE_ACCOUNT_INFO = json.loads(os.environ.get('GOOGLE_CREDENTIALS_JSON'))
    CALENDAR_ID = os.environ.get('GOOGLE_CALENDAR_ID')
    DATABASE_URL = os.environ.get('DATABASE_URL')

def get_db_connection():
    conn = psycopg2.connect(DATABASE_URL)
    return conn

    # Cria as credenciais a partir das informações da conta de serviço
    credentials = service_account.Credentials.from_service_account_info(
        SERVICE_ACCOUNT_INFO,
        scopes=['https://www.googleapis.com/auth/calendar']
     )
    
    # Constrói o serviço do Google Calendar
    service = build('calendar', 'v3', credentials=credentials)
    
except Exception as e:
    # Se houver um erro ao carregar as credenciais, a API não funcionará.
    # Esta mensagem aparecerá nos logs de erro do Render.
    print(f"ERRO CRÍTICO: Falha ao carregar credenciais ou variáveis de ambiente. {e}")
    service = None


# --- O ENDPOINT DA NOSSA API ---
# Este é o "endereço" que o Typebot vai chamar.
@app.route('/api/create-event', methods=['POST'])
def create_event():
    # Verifica se as credenciais foram carregadas corretamente
    if not service:
        return jsonify({"message": "Erro de configuração no servidor."}), 500

    # 1. Extrai os dados enviados pelo Typebot
    data = request.get_json()
    appointment_date_str = data.get('appointmentDate')
    client_name = data.get('clientName')
    client_phone = data.get('clientPhone')

    if not appointment_date_str or not client_name:
        return jsonify({"message": "Dados incompletos. 'appointmentDate' e 'clientName' são obrigatórios."}), 400

    try:
        # 2. Monta o objeto do evento para o Google Calendar
        start_time = datetime.fromisoformat(appointment_date_str.replace('Z', '+00:00'))
        end_time = start_time + timedelta(hours=1) # Define a duração da consulta para 1 hora

        event = {
            'summary': f'Consulta: {client_name}',
            'description': f'Agendado via Chatbot. Contato: {client_phone or "Não informado"}',
            'start': {
                'dateTime': start_time.isoformat(),
                'timeZone': 'America/Sao_Paulo', # IMPORTANTE: Mude para o seu fuso horário!
            },
            'end': {
                'dateTime': end_time.isoformat(),
                'timeZone': 'America/Sao_Paulo', # IMPORTANTE: Mude para o seu fuso horário!
            },
        }

        # 3. Insere o evento na agenda
        created_event = service.events().insert(calendarId=CALENDAR_ID, body=event).execute()

        # 4. Retorna uma resposta de sucesso
        return jsonify({
            "message": "Agendamento criado com sucesso!",
            "eventId": created_event['id'],
            "eventLink": created_event['htmlLink']
        }), 200

    except Exception as e:
        print(f"Erro ao criar evento: {e}") # Imprime o erro nos logs do Render
        return jsonify({"message": "Ocorreu um erro ao criar o agendamento.", "error": str(e)}), 500

# --- NOVA ROTA PARA BUSCAR PACIENTE POR TELEFONE ---
@app.route('/api/patient', methods=['GET'])
def get_patient_by_phone():
    # 1. Pega o número de telefone dos parâmetros da URL
    phone_number = request.args.get('phone')

    if not phone_number:
        return jsonify({"message": "Número de telefone não fornecido."}), 400

    try:
        # 2. Conecta ao banco de dados
        conn = get_db_connection()
        cur = conn.cursor() # Cria um "cursor" para executar comandos

        # 3. Executa a consulta SQL para encontrar o paciente
        cur.execute("SELECT id, full_name, email FROM patients WHERE phone = %s", (phone_number,))
        
        patient = cur.fetchone() # Pega o primeiro resultado encontrado

        # 4. Fecha a conexão
        cur.close()
        conn.close()

        # 5. Verifica o resultado e retorna a resposta
        if patient:
            # Se encontrou, retorna os dados do paciente
            patient_data = {
                "id": patient[0],
                "full_name": patient[1],
                "email": patient[2]
            }
            return jsonify(patient_data), 200
        else:
            # Se não encontrou, retorna uma mensagem clara
            return jsonify({"message": "Paciente não encontrado."}), 404

    except Exception as e:
        print(f"Erro ao buscar paciente: {e}")
        return jsonify({"message": "Erro interno no servidor."}), 500


# Rota de teste para verificar se a API está no ar
@app.route('/')
def index():
    return "API do Chatbot no ar!"

