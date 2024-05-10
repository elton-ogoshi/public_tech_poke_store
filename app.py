from fastapi import FastAPI
from pydantic import BaseModel, StringConstraints, ValidationError
from typing import Annotated, Literal
import requests
import json
import gspread
from google.oauth2 import service_account
from dotenv import load_dotenv
import os
import datetime
import pytz

# Load environment variables from .env file
load_dotenv()

# Database file given by rm number as key
DB_FILE = os.getenv("DB_FILE")

# Google Sheets ID
SHEET_ID = os.getenv("SHEET_ID")

# Path to the service account JSON key file
KEY_FILE = os.getenv("KEY_FILE")

# Sheet names for tech and poke stores
tech_store_sheet_name = "Tech Store"
poke_store_sheet_name = "Poke Store"

# Google Sheets API scope
scopes = ["https://www.googleapis.com/auth/spreadsheets"]

# Authenticate with Google Sheets API using service account
credentials = service_account.Credentials.from_service_account_file(KEY_FILE, scopes=scopes)
client = gspread.authorize(credentials)

# Function to read the database from a JSON file
def read_db() -> dict:
    with open(DB_FILE, 'r') as file:
        return json.load(file)

# Function to write the database to a JSON file
def write_db(db: dict):
    with open(DB_FILE, 'w') as file:
        json.dump(db, file)

# Initialize FastAPI app
app = FastAPI(root_path="/api")

# Regular expression patterns for CEP and RM
CEP = Annotated[str, StringConstraints(pattern=r'^\d{8}$')]
RM = Annotated[str, StringConstraints(pattern=r'^\d{5,6}$')]

# InputBase model for the action field
class InputBase(BaseModel):
    action: Literal['get_address', 'save_address', 'check_registration', 'make_order_tech', 'make_order_poke']

# Payload models for each action
class PayloadGetAddress(BaseModel):
    cep: CEP

class PayloadSaveAddress(BaseModel):
    rm: RM
    cep: CEP
    numero: int
    nome: str

class PayloadCheckRegistration(BaseModel):
    rm: RM

class PayloadMakeOrderTech(BaseModel):
    rm: RM
    produto: str
    marca: str
    valor: float

class PayloadMakeOrderPoke(BaseModel):
    rm: RM
    tamanho: str
    base: str
    topping: str
    crunch: str
    proteina: str
    molho: str
    valor: float

# Input models for each action combining InputBase and corresponding Payload model
class InputGetAddress(InputBase, PayloadGetAddress):
    action: Literal['get_address'] = 'get_address'

class InputSaveAddress(InputBase, PayloadSaveAddress):
    action: Literal['save_address'] = 'save_address'

class InputCheckRegistration(InputBase, PayloadCheckRegistration):
    action: Literal['check_registration'] = 'check_registration'

class InputMakeOrderTech(InputBase, PayloadMakeOrderTech):
    action: Literal['make_order_tech'] = 'make_order_tech'

class InputMakeOrderPoke(InputBase, PayloadMakeOrderPoke):
    action: Literal['make_order_poke'] = 'make_order_poke'

# Function to check registration status of a user given their RM number
def check_registration(rm: str):
    db = read_db()
    if rm not in db.keys():
        return {"error": True, "status": "not_found", "detail": f"RM {rm} not found in the database."}
    elif db[rm] is None:
        return {"error": True, "status": "unregistered_address", "detail": f"RM {rm} found in the database, but its address is None."}
    else:
        return {"error": False, "status": "registered", "detail": f"RM {rm} found in the database.", "data": db[rm]}

# Function to fetch address details from ViaCEP API given a CEP
def get_address(cep: str):
    try:
        response = requests.get(f'https://viacep.com.br/ws/{cep}/json')
        response.raise_for_status()
        data = response.json()
        if 'erro' in data.keys():
            return {"error": True, "detail": "CEP not found."}
        address = {
            "cep": cep,
            "rua": data["logradouro"],
            "bairro": data["bairro"],
            "cidade": data["localidade"],
            "estado": data["uf"],
        }
        return {"error": False, "data": address}
    except requests.exceptions.RequestException as err:
        return {"error": True, "detail": str(err)}

# Function to save the address details for a user given their RM number
def save_address(payload: PayloadSaveAddress):
    db = read_db()
    check_result = check_registration(payload.rm)
    if check_result["status"] == "not_found":
        return check_result
    else:
        # Get the address details from the get_address function
        address_details = get_address(payload.cep)
        if address_details["error"]:
            return address_details
        else:
            address = {
                "cep": payload.cep,
                "rua": address_details["data"]["rua"],
                "bairro": address_details["data"]["bairro"],
                "cidade": address_details["data"]["cidade"],
                "estado": address_details["data"]["estado"],
                "numero": payload.numero,
                "nome": payload.nome
            }
            db[payload.rm] = address
            write_db(db)
            return {"error": False, "status": "updated", "data": f"Address for RM {payload.rm} updated successfully."}

# Function to create a new order for the tech store
def make_order_tech(payload: PayloadMakeOrderTech, sheet_name: str = tech_store_sheet_name):
    check_result = check_registration(payload.rm)

    if check_result["error"]:
        return check_result

    data = check_result["data"]

    try:
        sheet = client.open_by_key(SHEET_ID).worksheet(sheet_name)
        # Get the current timestamp in the São Paulo timezone
        sao_paulo_tz = pytz.timezone('America/Sao_Paulo')
        timestamp = datetime.datetime.now(sao_paulo_tz)

        order = [timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                 payload.rm,
                 payload.produto,
                 payload.marca,
                 payload.valor,
                 data["cep"],
                 data["rua"],
                 data["numero"],
                 data["bairro"],
                 data["cidade"],
                 data["estado"],
                 data["nome"]
                 ]

        sheet.append_row(order)

        return {"error": False, "status": "order_made", "detail": f"Order for RM {payload.rm} made successfully."}

    except gspread.exceptions.APIError as e:
        return {"error": True, "status": "api_error", "detail": f"Error occurred while accessing Google Sheets API: {str(e)}"}

    except KeyError as e:
        return {"error": True, "status": "missing_data", "detail": f"Missing data in address details: {str(e)}"}

    except Exception as e:
        return {"error": True, "status": "unknown_error", "detail": f"An unknown error occurred: {str(e)}"}

# Function to create a new order for the poke store
def make_order_poke(payload: PayloadMakeOrderPoke, sheet_name: str = poke_store_sheet_name):
    check_result = check_registration(payload.rm)

    if check_result["error"]:
        return check_result

    data = check_result["data"]

    try:
        sheet = client.open_by_key(SHEET_ID).worksheet(sheet_name)
        # Get the current timestamp in the São Paulo timezone
        sao_paulo_tz = pytz.timezone('America/Sao_Paulo')
        timestamp = datetime.datetime.now(sao_paulo_tz)

        order = [timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                 payload.rm,
                 payload.tamanho,
                 payload.base,
                 payload.topping,
                 payload.crunch,
                 payload.proteina,
                 payload.molho,
                 payload.valor,
                 data["cep"],
                 data["rua"],
                 data["numero"],
                 data["bairro"],
                 data["cidade"],
                 data["estado"],
                 data["nome"]
                 ]

        sheet.append_row(order)

        return {"error": False, "status": "order_made", "detail": f"Order for RM {payload.rm} made successfully."}

    except gspread.exceptions.APIError as e:
        return {"error": True, "status": "api_error", "detail": f"Error occurred while accessing Google Sheets API: {str(e)}"}

    except KeyError as e:
        return {"error": True, "status": "missing_data", "detail": f"Missing data in address details: {str(e)}"}

    except Exception as e:
        return {"error": True, "status": "unknown_error", "detail": f"An unknown error occurred: {str(e)}"}

# Mapping of action to their respective input models
input_model_mapping = {
    "get_address": InputGetAddress,
    "save_address": InputSaveAddress,
    "check_registration": InputCheckRegistration,
    "make_order_tech": InputMakeOrderTech,
    "make_order_poke": InputMakeOrderPoke,
}

# FastAPI endpoint to handle incoming requests
@app.post("/")
def api_endpoint(input_data: InputGetAddress | InputSaveAddress | InputCheckRegistration | InputMakeOrderTech | InputMakeOrderPoke):
    action = input_data.action

    if action not in input_model_mapping:
        return {"action": action, "response_payload": {"error": True, "detail": "Invalid action"}}

    # Using the input_data instead of parsed_input_data
    match action:
        case 'get_address':
            result = get_address(input_data.cep)
            return {"action": action, "response_payload": result}
        case 'save_address':
            result = save_address(input_data)
            return {"action": action, "response_payload": result}
        case 'check_registration':
            result = check_registration(input_data.rm)
            return {"action": action, "response_payload": result}
        case 'make_order_tech':
            result = make_order_tech(input_data)
            return {"action": action, "response_payload": result}
        case 'make_order_poke':
            result = make_order_poke(input_data)
            return {"action": action, "response_payload": result}
        case _:
            return {"action": action, "response_payload": {"error": True, "detail": "Invalid action"}}