import os
import io
import werkzeug
import requests
import pandas as pd
import smtplib
import threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import Flask, render_template, request, redirect, url_for, send_file
from supabase import create_client, Client
from dotenv import load_dotenv

# 1. Cargar Credenciales
load_dotenv()
app = Flask(__name__)

url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(url, key)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

EMAIL_REMITENTE = os.environ.get("EMAIL_REMITENTE")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")
EMAIL_DESTINO = os.environ.get("EMAIL_DESTINO")

BUCKET_FOTOS = "evidencias"

# --- MÓDULO DE ALERTAS (TELEGRAM Y CORREO) ---

def enviar_alerta_telegram(empresa, tipo_reporte, nombre, nombre_reportado, team, descripcion, foto_url):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return 
        
    mensaje = f"🚨 NUEVO REPORTE SST 🚨\n\n"
    mensaje += f"🏢 Empresa: {empresa}\n"
    if team:
        mensaje += f"👥 Team: {team}\n"
    mensaje += f"⚠️ Tipo: {tipo_reporte}\n"
    mensaje += f"🕵️ Reporta: {nombre}\n"
    if nombre_reportado:
        mensaje += f"👤 Involucrado: {nombre_reportado}\n"
    mensaje += f"📝 Detalle: {descripcion}\n"
    if foto_url:
        mensaje += f"\n📷 Evidencia: {foto_url}"
        
    url_api = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": mensaje}
    try:
        requests.post(url_api, json=payload)
    except Exception as e:
        print(f"[-] Error conectando con Telegram: {e}")

def enviar_correo_background(empresa, tipo_reporte, nombre, nombre_reportado, team, descripcion, foto_url):
    if not EMAIL_REMITENTE or not EMAIL_PASSWORD or not EMAIL_DESTINO:
        return

    asunto = f"🚨 NUEVO REPORTE SST: {empresa} - {tipo_reporte}"
    cuerpo = f"""
    Se ha registrado un nuevo reporte en el Sistema de Inteligencia SST.
    
    🏢 EMPRESA: {empresa}
    👥 TEAM: {team if team else 'N/A'}
    ⚠️ TIPO DE REPORTE: {tipo_reporte}
    🕵️ TRABAJADOR QUE REPORTA: {nombre}
    👤 TRABAJADOR REPORTADO: {nombre_reportado if nombre_reportado else 'N/A'}
    
    📝 DESCRIPCIÓN DEL EVENTO:
    {descripcion}
    
    📷 ENLACE A LA EVIDENCIA (FOTO):
    {foto_url if foto_url else 'El trabajador no adjuntó fotografía.'}
    
    ---
    Este es un mensaje automático del Sistema SST.
    """

    msg = MIMEMultipart()
    msg['From'] = EMAIL_REMITENTE
    msg['To'] = EMAIL_DESTINO
    msg['Subject'] = asunto
    msg.attach(MIMEText(cuerpo, 'plain'))

    try:
        # Usamos SMTP_SSL en puerto 465 para evitar bloqueos del servidor
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465, timeout=10)
        server.login(EMAIL_REMITENTE, EMAIL_PASSWORD)
        server.send_message(msg)
        server.quit()
        print("[+] Correo enviado con éxito de fondo.")
    except Exception as e:
        print(f"[-] Error enviando el correo: {e}")

def enviar_alerta_correo(empresa, tipo_reporte, nombre, nombre_reportado, team, descripcion, foto_url):
    # Ejecuta el envío de correo en un hilo separado para no congelar la pantalla web
    hilo = threading.Thread(
        target=enviar_correo_background, 
        args=(empresa, tipo_reporte, nombre, nombre_reportado, team, descripcion, foto_url)
    )
    hilo.start()

# --- MÓDULO DE FOTOS ---

def subir_foto_a_supabase(archivo_foto, empresa, tipo_reporte):
    try:
        nombre_original = werkzeug.utils.secure_filename(archivo_foto.filename)
        if not nombre_original:
            return None
        ruta_remota = f"{empresa}/{tipo_reporte}_{nombre_original}"
        datos_archivo = archivo_foto.read()
        supabase.storage.from_(BUCKET_FOTOS).upload(
            path=ruta_remota,
            file=datos_archivo,
            file_options={"content-type": archivo_foto.content_type, "x-upsert": "true"}
        )
        return supabase.storage.from_(BUCKET_FOTOS).get_public_url(ruta_remota)
    except Exception as e:
        print(f"[-] Error: {str(e)}")
        return None

# --- RUTAS PÚBLICAS (FORMULARIOS) ---

@app.route('/')
def inicio():
    return "Sistema de SST Activo."

@app.route('/terpesa/reportar', methods=['GET', 'POST'])
def ruta_terpesa():
    if request.method == 'POST':
        return procesar_reporte('TERPESA', request)
    respuesta = supabase.table('trabajadores').select('nombre_completo').eq('empresa', 'TERPESA').execute()
    return render_template('formulario.html', empresa='TERPESA', trabajadores=respuesta.data)

@app.route('/simecar/reportar', methods=['GET', 'POST'])
def ruta_simecar():
    if request.method == 'POST':
        return procesar_reporte('SIMECAR', request)
    respuesta = supabase.table('trabajadores').select('nombre_completo').eq('empresa', 'SIMECAR').execute()
    return render_template('formulario.html', empresa='SIMECAR', trabajadores=respuesta.data)

def procesar_reporte(empresa, req):
    tipo_form = req.form.get('tipo_reporte')
    fecha = req.form.get('fecha')
    hora = req.form.get('hora')
    nombre_reportante = req.form.get('reportante')
    nombre_reportado = req.form.get('reportado')
    sitio = req.form.get('sitio')
    descripcion = req.form.get('descripcion')
    es_anonimo = req.form.get('anonimo') 
    team = req.form.get('team')
    archivo_foto = req.files.get('evidencia')

    try:
        id_reportante = None
        if not es_anonimo:
            busqueda_reportante = supabase.table('trabajadores').select('id').eq('nombre_completo', nombre_reportante).eq('empresa', empresa).execute()
            if not busqueda_reportante.data:
                return "Error: El trabajador que reporta no está registrado.", 400
            id_reportante = busqueda_reportante.data[0]['id']

        id_reportado = None
        if tipo_form == 'ACTO' and nombre_reportado:
            busqueda_reportado = supabase.table('trabajadores').select('id').eq('nombre_completo', nombre_reportado).eq('empresa', empresa).execute()
            if busqueda_reportado.data:
                id_reportado = busqueda_reportado.data[0]['id']

        url_evidencia = subir_foto_a_supabase(archivo_foto, empresa, tipo_form) if archivo_foto and archivo_foto.filename != '' else None

        nuevo_reporte = {
            "tipo_reporte": "ACTO_INSEGURO" if tipo_form == 'ACTO' else "CONDICION_INSEGURA",
            "empresa": empresa,
            "fecha_ocurrencia": fecha,
            "hora_ocurrencia": hora,
            "id_reportante": id_reportante,
            "id_reportado": id_reportado,
            "sitio_zonal": sitio if tipo_form == 'CONDICION' else None,
            "descripcion": descripcion,
            "evidencia_url": url_evidencia,
            "team": team
        }
        
        supabase.table('reportes').insert(nuevo_reporte).execute()
        
        tipo_alerta = "ACTO INSEGURO" if tipo_form == 'ACTO' else "CONDICION INSEGURA"
        nombre_alerta = "Anónimo 🕵️" if es_anonimo else nombre_reportante
        
        # Se disparan ambas notificaciones
        enviar_alerta_telegram(empresa, tipo_alerta, nombre_alerta, nombre_reportado, team, descripcion, url_evidencia)
        enviar_alerta_correo(empresa, tipo_alerta, nombre_alerta, nombre_reportado, team, descripcion, url_evidencia)

        return "<h1>¡Reporte enviado con éxito!</h1>"
    except Exception as e:
        return f"Hubo un error: {str(e)}", 500

# --- RUTAS PRIVADAS (DASHBOARD Y GESTIÓN) ---

@app.route('/admin/dashboard')
def dashboard():
    try:
        res_reportes = supabase.table('reportes').select('*').order('id', desc=True).execute()
        res_trabajadores = supabase.table('trabajadores').select('id, nombre_completo').execute()
        
        mapa_trabajadores = {t['id']: t['nombre_completo'] for t in res_trabajadores.data}
        
        reportes = res_reportes.data
        for r in reportes:
            r['nombre_reportante'] = mapa_trabajadores.get(r.get('id_reportante'), 'Anónimo')
            r['nombre_reportado'] = mapa_trabajadores.get(r.get('id_reportado'), 'N/A')
            
        return render_template('dashboard.html', reportes=reportes)
    except Exception as e:
        return f"Error al cargar dashboard: {str(e)}"

@app.route('/admin/reporte/editar/<int:id_reporte>', methods=['GET', 'POST'])
def editar_reporte(id_reporte):
    if request.method == 'POST':
        fecha = request.form.get('fecha')
        hora = request.form.get('hora')
        team = request.form.get('team')
        sitio = request.form.get('sitio')
        descripcion = request.form.get('descripcion')
        
        datos_actualizados = {
            "fecha_ocurrencia": fecha,
            "hora_ocurrencia": hora,
            "team": team if team else None,
            "sitio_zonal": sitio if sitio else None,
            "descripcion": descripcion
        }
        
        try:
            supabase.table('reportes').update(datos_actualizados).eq('id', id_reporte).execute()
            return redirect(url_for('dashboard'))
        except Exception as e:
            return f"Error al guardar los cambios: {str(e)}"
    else:
        try:
            respuesta = supabase.table('reportes').select('*').eq('id', id_reporte).execute()
            if not respuesta.data:
                return "Reporte no encontrado."
            return render_template('editar.html', reporte=respuesta.data[0])
        except Exception as e:
            return f"Error al cargar el reporte: {str(e)}"

@app.route('/admin/reporte/eliminar/<int:id_reporte>')
def eliminar_reporte(id_reporte):
    try:
        supabase.table('reportes').delete().eq('id', id_reporte).execute()
        return redirect(url_for('dashboard'))
    except Exception as e:
        return f"Error al eliminar el reporte: {str(e)}"

@app.route('/admin/trabajadores', methods=['GET', 'POST'])
def gestionar_trabajadores():
    if request.method == 'POST':
        nombre = request.form.get('nombre')
        empresa = request.form.get('empresa')
        try:
            supabase.table('trabajadores').insert({"nombre_completo": nombre.upper(), "empresa": empresa}).execute()
            return redirect(url_for('gestionar_trabajadores'))
        except Exception as e:
            return f"Error al agregar trabajador: {str(e)}"
    
    try:
        respuesta = supabase.table('trabajadores').select('*').order('empresa').order('nombre_completo').execute()
        return render_template('trabajadores.html', trabajadores=respuesta.data)
    except Exception as e:
        return f"Error al cargar la lista de trabajadores: {str(e)}"

@app.route('/admin/trabajadores/eliminar/<int:id_trabajador>')
def eliminar_trabajador(id_trabajador):
    try:
        supabase.table('trabajadores').delete().eq('id', id_trabajador).execute()
        return redirect(url_for('gestionar_trabajadores'))
    except Exception as e:
        return f"Error al eliminar trabajador: {str(e)}"

@app.route('/admin/exportar')
def exportar_excel():
    try:
        res_reportes = supabase.table('reportes').select('*').order('id', desc=True).execute()
        res_trabajadores = supabase.table('trabajadores').select('id, nombre_completo').execute()
        
        mapa_trabajadores = {t['id']: t['nombre_completo'] for t in res_trabajadores.data}
        
        datos_excel = []
        for r in res_reportes.data:
            datos_excel.append({
                "N° Reporte": r.get('id'),
                "Empresa": r.get('empresa'),
                "Team": r.get('team', 'N/A'),
                "Tipo de Reporte": r.get('tipo_reporte'),
                "Fecha": r.get('fecha_ocurrencia'),
                "Hora": r.get('hora_ocurrencia'),
                "Reportante (Testigo)": mapa_trabajadores.get(r.get('id_reportante'), 'Anónimo'),
                "Involucrado (Reportado)": mapa_trabajadores.get(r.get('id_reportado'), 'N/A'),
                "Sitio / Zonal": r.get('sitio_zonal', 'N/A'),
                "Descripción del Evento": r.get('descripcion'),
                "Enlace de Evidencia": r.get('evidencia_url', 'Sin evidencia')
            })

        df = pd.DataFrame(datos_excel)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Reportes SST')
        
        output.seek(0)
        
        return send_file(
            output,
            as_attachment=True,
            download_name="Registro_Inteligencia_SST.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    except Exception as e:
        return f"Error al exportar a Excel: {str(e)}"

if __name__ == '__main__':
    app.run(debug=True, port=5000)
