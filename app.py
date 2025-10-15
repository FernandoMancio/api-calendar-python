import os
import json
import psycopg2
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from google.oauth2 import service_account
from googleapiclient.discovery import build
from flask_cors import CORS

# Inicializa a aplicação Flask
app = Flask(__name__)

# Habilita o CORS para todas as rotas da aplicação
CORS(app)

# --- CARREGANDO VARIÁVEIS DE AMBIENTE ---
# Pega as credenciais e configurações do ambiente do Render de forma segura
GOOGLE_CREDENTIALS_JSON = os.environ.get('GOOGLE_CREDENTIALS_JSON')
GOOGLE_CALENDAR_ID = os.environ.get('GOOGLE_CALENDAR_ID')
DATABASE_URL = os.environ.get('DATABASE_URL')

# --- INICIALIZAÇÃO DOS SERVIÇOS ---
# Define variáveis globais para os serviços, para que possam ser usadas em todas as rotas
google_service = None
db_conn_error = None

# Tenta inicializar o serviço do Google Calendar ao iniciar a aplicação
try:
    if not GOOGLE_CREDENTIALS_JSON:
        raise ValueError("Variável de ambiente GOOGLE_CREDENTIALS_JSON não encontrada.")
    
    service_account_info = json.loads(GOOGLE_CREDENTIALS_JSON)
    credentials = service_account.Credentials.from_service_account_info(
        service_account_info,
        scopes=['https://www.googleapis.com/auth/calendar']
     )
    google_service = build('calendar', 'v3', credentials=credentials)
    print("Serviço do Google Calendar inicializado com sucesso.")

except Exception as e:
    print(f"ERRO CRÍTICO ao inicializar o Google Calendar: {e}")


# Tenta verificar a conexão com o banco de dados ao iniciar a aplicação
try:
    if not DATABASE_URL:
        raise ValueError("Variável de ambiente DATABASE_URL não encontrada.")
    
    conn = psycopg2.connect(DATABASE_URL)
    conn.close() # Apenas testa a conexão e fecha imediatamente
    print("Conexão com o banco de dados Supabase testada com sucesso.")

except Exception as e:
    db_conn_error = str(e)
    print(f"ERRO CRÍTICO ao conectar com o banco de dados: {e}")


# --- FUNÇÃO HELPER PARA CONEXÃO COM O BANCO DE DADOS ---
# Esta função será chamada toda vez que uma rota precisar interagir com o banco
def get_db_connection():
    if db_conn_error:
        raise Exception(f"A conexão com o banco de dados falhou na inicialização: {db_conn_error}")
    conn = psycopg2.connect(DATABASE_URL)
    return conn


# --- ROTA PARA BUSCAR PACIENTE POR TELEFONE ---
@app.route('/api/patient', methods=['POST'])
def get_patient_by_phone():
    # Pega os dados enviados pelo Typebot
    data = request.get_json()
    phone_number = data.get('phone')

    if not phone_number:
        return jsonify({"message": "Número de telefone não fornecido no corpo da requisição."}), 400
    
#@app.route('/api/patient', methods=['GET'])
#def get_patient_by_phone():
#    phone_number = request.args.get('phone')

#    if not phone_number:
#        return jsonify({"message": "Número de telefone não fornecido."}), 400

    try:
        conn = get_db_connection()
        cur = conn.cursor()
    
        # A linha abaixo é a única que você precisa alterar
        sql_query = 'SELECT "CadCli_NmPrefer", "CadCli_Email" FROM "dCadastro_Cli" WHERE "CadCli_Celular" = %s'
        
        cur.execute(sql_query, (phone_number,))
        patient = cur.fetchone()

        cur.close()
        conn.close()

        if patient:
            # Monta a resposta JSON com as chaves que o Typebot vai usar
            patient_data = {
                "preferred_name": patient[0],
                "email": patient[1]
            }
            return jsonify(patient_data), 200
        else:
            return jsonify({"message": "Paciente não encontrado."}), 404

    except Exception as e:
        print(f"Erro ao buscar paciente: {e}")
        return jsonify({"message": "Erro interno no servidor."}), 500


# --- ROTA PARA CRIAR EVENTO NO GOOGLE CALENDAR ---
# (Esta rota ainda não está conectada ao banco de dados, mas está funcional)
@app.route('/api/create-event', methods=['POST'])
def create_event():
    if not google_service:
        return jsonify({"message": "Erro de configuração no servidor (Google Service não disponível)."}), 500

    data = request.get_json()
    appointment_date_str = data.get('appointmentDate')
    client_name = data.get('clientName')
    client_phone = data.get('clientPhone')

    if not appointment_date_str or not client_name:
        return jsonify({"message": "Dados incompletos. 'appointmentDate' e 'clientName' são obrigatórios."}), 400

    try:
        start_time = datetime.fromisoformat(appointment_date_str.replace('Z', '+00:00'))
        end_time = start_time + timedelta(hours=1)

        event = {
            'summary': f'Consulta: {client_name}',
            'description': f'Agendado via Chatbot. Contato: {client_phone or "Não informado"}',
            'start': { 'dateTime': start_time.isoformat(), 'timeZone': 'America/Sao_Paulo' },
            'end': { 'dateTime': end_time.isoformat(), 'timeZone': 'America/Sao_Paulo' },
        }

        created_event = google_service.events().insert(calendarId=GOOGLE_CALENDAR_ID, body=event).execute()

        return jsonify({
            "message": "Agendamento criado com sucesso!",
            "eventId": created_event['id'],
            "eventLink": created_event['htmlLink']
        }), 200

    except Exception as e:
        print(f"Erro ao criar evento: {e}")
        return jsonify({"message": "Ocorreu um erro ao criar o agendamento.", "error": str(e)}), 500

# --- NOVA ROTA PARA CRIAR UM NOVO PACIENTE ---
@app.route('/api/create-patient', methods=['POST'])
def create_patient():
    # 1. Pega os dados enviados pelo Typebot no corpo da requisição
    data = request.get_json()
    full_name = data.get('fullName')
    phone = data.get('phone')
    email = data.get('email')
    preferred_name = data.get('preferredName')

    # 2. Validação simples para garantir que os dados essenciais foram enviados
    if not full_name or not phone or not email:
        return jsonify({"message": "Dados incompletos. Nome, telefone e email são obrigatórios."}), 400

    try:
        # 3. Conecta ao banco de dados
        conn = get_db_connection()
        cur = conn.cursor()

        # 4. Define a consulta SQL para INSERIR um novo registro
        # Usamos aspas duplas para garantir a correspondência com as colunas no Supabase
        sql_query = """
            INSERT INTO "dCadastro_Cli" ("CadCli_Nome", "CadCli_Celular", "CadCli_Email", "CadCli_NmPrefer")
            VALUES (%s, %s, %s, %s)
            RETURNING "CadCli_ID"; 
        """
        # O RETURNING "CadCli_ID" é opcional, mas é uma boa prática para confirmar que a inserção funcionou e obter o ID do novo cliente.

        # 5. Executa a consulta, passando os dados de forma segura
        cur.execute(sql_query, (full_name, phone, email, preferred_name or full_name)) # Se o nome preferido não for informado, usa o nome completo

        # 6. Pega o ID retornado
        new_patient_id = cur.fetchone()[0]

        # 7. Confirma a transação no banco de dados
        conn.commit()

        # 8. Fecha a conexão
        cur.close()
        conn.close()

        # 9. Retorna uma resposta de sucesso para o Typebot
        return jsonify({
            "message": "Paciente criado com sucesso!",
            "patientId": new_patient_id
        }), 201 # 201 Created é o código de status correto para criação

    except Exception as e:
        print(f"Erro ao criar paciente: {e}")
        return jsonify({"message": "Erro interno no servidor ao criar paciente."}), 500




# --- ROTA DE TESTE PARA VERIFICAR SE A API ESTÁ NO AR ---
@app.route('/')
def index():
    return "API do Chatbot no ar!"








