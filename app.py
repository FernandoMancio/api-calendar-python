import os
import json
import psycopg2
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from flask_cors import CORS
from google.oauth2 import service_account
from googleapiclient.discovery import build

# -----------------------------------------------------------------------------
# INICIALIZAÇÃO E CONFIGURAÇÃO
# -----------------------------------------------------------------------------

# Inicializa a aplicação Flask
app = Flask(__name__)

# Habilita o CORS para todas as rotas, permitindo a comunicação com o Typebot
CORS(app)

# Variáveis globais para os serviços e conexão com o banco
google_service = None
db_connection_string = None
db_connection_ok = False

try:
    # Carrega as credenciais do Google a partir das variáveis de ambiente
    SERVICE_ACCOUNT_INFO = json.loads(os.environ.get('GOOGLE_CREDENTIALS_JSON'))
    CALENDAR_ID = os.environ.get('GOOGLE_CALENDAR_ID')
    
    # Cria as credenciais para a API do Google Calendar
    google_credentials = service_account.Credentials.from_service_account_info(
        SERVICE_ACCOUNT_INFO,
        scopes=['https://www.googleapis.com/auth/calendar']
     )
    # Constrói o serviço do Google Calendar
    google_service = build('calendar', 'v3', credentials=google_credentials)
    print("Serviço do Google Calendar inicializado com sucesso.")

except Exception as e:
    print(f"ERRO CRÍTICO ao inicializar o serviço do Google Calendar: {e}")

try:
    # Carrega a string de conexão do banco de dados
    db_connection_string = os.environ.get('DATABASE_URL')
    if not db_connection_string:
        raise ValueError("Variável de ambiente DATABASE_URL não encontrada.")
    
    # Testa a conexão com o banco de dados na inicialização
    conn = psycopg2.connect(db_connection_string)
    conn.close()
    db_connection_ok = True
    print("Conexão com o banco de dados Supabase testada com sucesso.")

except Exception as e:
    print(f"ERRO CRÍTICO ao conectar com o banco de dados: {e}")

# Função auxiliar para obter uma nova conexão com o banco de dados
def get_db_connection():
    if not db_connection_ok:
        raise Exception("A conexão com o banco de dados falhou na inicialização.")
    return psycopg2.connect(db_connection_string)

# -----------------------------------------------------------------------------
# ROTAS DA API
# -----------------------------------------------------------------------------

# Rota de teste para verificar se a API está no ar
@app.route('/')
def index():
    return "API do Chatbot no ar! Conexão com DB: " + ("OK" if db_connection_ok else "FALHOU")

# --- ROTA PARA BUSCAR PACIENTE EXISTENTE POR TELEFONE ---
@app.route('/api/patient', methods=['POST'])
def get_patient_by_phone():
    data = request.get_json()
    phone_number = data.get('phone')

    if not phone_number:
        return jsonify({"message": "Número de telefone não fornecido no corpo da requisição."}), 400

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Consulta ajustada para os nomes exatos da sua tabela e colunas
        sql_query = 'SELECT "CadCli_NmPrefer", "CadCli_Email" FROM "dCadastro_Cli" WHERE "CadCli_Celular" = %s'
        
        cur.execute(sql_query, (phone_number,))
        patient = cur.fetchone()
        cur.close()
        conn.close()

        if patient:
            patient_data = {
                "preferred_name": patient[0],
                "email": patient[1]
            }
            return jsonify({"data": patient_data}), 200
        else:
            return jsonify({"message": "Paciente não encontrado."}), 404

    except Exception as e:
        print(f"Erro ao buscar paciente: {e}")
        return jsonify({"message": "Erro interno no servidor."}), 500

# --- ROTA PARA CRIAR UM NOVO PACIENTE ---
@app.route('/api/create-patient', methods=['POST'])
def create_patient():
    data = request.get_json()
    full_name = data.get('fullName')
    phone = data.get('phone')
    email = data.get('email')
    preferred_name = data.get('preferredName')

    if not full_name or not phone or not email:
        return jsonify({"message": "Dados incompletos. Nome, telefone e email são obrigatórios."}), 400

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        sql_query = """
            INSERT INTO "dCadastro_Cli" ("CadCli_Nome", "CadCli_Celular", "CadCli_Email", "CadCli_NmPrefer")
            VALUES (%s, %s, %s, %s)
            RETURNING "CadCli_ID"; 
        """
        
        cur.execute(sql_query, (full_name, phone, email, preferred_name or full_name))
        new_patient_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()

        return jsonify({
            "message": "Paciente criado com sucesso!",
            "patientId": new_patient_id
        }), 201

    except Exception as e:
        print(f"Erro ao criar paciente: {e}")
        return jsonify({"message": "Erro interno no servidor ao criar paciente."}), 500

# --- ROTA PARA BUSCAR TODOS OS DIAS DISPONÍVEIS ---
@app.route('/api/available-dates', methods=['GET'])
def get_available_dates():
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        sql_query = """
            SELECT DISTINCT TO_CHAR(T1."Agen_Data", 'YYYY-MM-DD') AS available_date
            FROM "dAgenda" AS T1
            LEFT JOIN "fConsulta" AS T2 ON T1."Agen_ID" = T2."Agen_ID"
            WHERE T1."Agen_Data" >= NOW()::date
              AND (T2."Agen_ID" IS NULL OR T2."Cons_Status" = 'Cancelado')
            ORDER BY available_date;
        """

        cur.execute(sql_query)
        dates = cur.fetchall()
        cur.close()
        conn.close()

        available_dates = [date[0] for date in dates]
        return jsonify({"available_dates": available_dates}), 200

    except Exception as e:
        print(f"Erro ao buscar datas disponíveis: {e}")
        return jsonify({"message": "Erro interno no servidor."}), 500

# --- ROTA PARA BUSCAR HORÁRIOS DISPONÍVEIS EM UM DIA ---
@app.route('/api/available-times', methods=['GET'])
def get_available_times():
    selected_date = request.args.get('date')

    if not selected_date:
        return jsonify({"message": "Data é obrigatória."}), 400

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        sql_query = """
            SELECT TO_CHAR(T1."Agen_Horario", 'HH24:MI') AS start_hour
            FROM "dAgenda" AS T1
            LEFT JOIN "fConsulta" AS T2 ON T1."Agen_ID" = T2."Agen_ID"
            WHERE T1."Agen_Data"::date = %s::date
              AND (T2."Agen_ID" IS NULL OR T2."Cons_Status" = 'Cancelado')
            ORDER BY start_hour;
        """

        cur.execute(sql_query, (selected_date,))
        times = cur.fetchall()
        cur.close()
        conn.close()

        available_times = [time[0] for time in times]
        return jsonify({"available_times": available_times}), 200

    except Exception as e:
        print(f"Erro ao buscar horários disponíveis: {e}")
        return jsonify({"message": "Erro interno no servidor."}), 500

# -----------------------------------------------------------------------------
# FIM DAS ROTAS
# -----------------------------------------------------------------------------
