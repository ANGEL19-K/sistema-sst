import os
import werkzeug
import requests
from flask import Flask, render_template, request, redirect, url_for
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(url, key)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

BUCKET_FOTOS = "evidencias"

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
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": mensaje
    }
    try:
        requests.post(url_api, json=payload)
    except Exception as e:
        print(f"[-] Error conectando con Telegram: {e}")

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
        enviar_alerta_telegram(empresa, tipo_alerta, nombre_alerta, nombre_reportado, team, descripcion, url_evidencia)

        return "<h1>¡Reporte enviado con éxito!</h1>"
    except Exception as e:
        return f"Hubo un error: {str(e)}", 500

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

# --- NUEVAS FUNCIONES: GESTIÓN DE TRABAJADORES ---
@app.route('/admin/trabajadores', methods=['GET', 'POST'])
def gestionar_trabajadores():
    if request.method == 'POST':
        nombre = request.form.get('nombre')
        empresa = request.form.get('empresa')
        try:
            # Convierte el nombre a mayúsculas para mantener orden en la BD
            supabase.table('trabajadores').insert({"nombre_completo": nombre.upper(), "empresa": empresa}).execute()
            return redirect(url_for('gestionar_trabajadores'))
        except Exception as e:
            return f"Error al agregar trabajador: {str(e)}"
    
    try:
        # Trae la lista ordenada por empresa y luego alfabéticamente
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
        # Nota técnica: Si el trabajador tiene reportes vinculados, Supabase podría bloquear su eliminación 
        # por integridad de datos. Si sale error, avísame y activamos el borrado en cascada.
        return f"Error al eliminar trabajador. Asegúrate de que no tenga reportes asociados: {str(e)}"

if __name__ == '__main__':
    app.run(debug=True, port=5000)
