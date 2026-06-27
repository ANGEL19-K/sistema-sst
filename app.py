import os
import io
import json
import werkzeug
import requests
import pandas as pd
import threading
from flask import Flask, render_template, request, redirect, url_for, send_file
from supabase import create_client, Client
from dotenv import load_dotenv
from datetime import datetime, timedelta

# 1. Cargar Credenciales
load_dotenv()
app = Flask(__name__)

url = os.environ.get("SUPABASE_URL")
key = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(url, key)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Usamos la API de Google para correos (Evita el bloqueo de Render)
GOOGLE_SCRIPT_URL = os.environ.get("GOOGLE_SCRIPT_URL")
EMAIL_DESTINO = os.environ.get("EMAIL_DESTINO")

BUCKET_FOTOS = "evidencias"

# =======================================================
# --- MÓDULO DE ALERTAS (TELEGRAM Y CORREO) ---
# =======================================================

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
    if not GOOGLE_SCRIPT_URL or not EMAIL_DESTINO:
        return

    asunto = f"🚨 NUEVO REPORTE SST: {empresa} - {tipo_reporte}"
    cuerpo = f"""Se ha registrado un nuevo reporte en el Sistema de Inteligencia SST.

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
Este es un mensaje automático del Sistema SST."""

    payload = {
        "destino": EMAIL_DESTINO,
        "asunto": asunto,
        "cuerpo": cuerpo
    }

    try:
        requests.post(GOOGLE_SCRIPT_URL, json=payload, timeout=10)
    except Exception as e:
        print(f"[-] Error enviando el correo por Google: {e}")

def enviar_alerta_correo(empresa, tipo_reporte, nombre, nombre_reportado, team, descripcion, foto_url):
    hilo = threading.Thread(
        target=enviar_correo_background, 
        args=(empresa, tipo_reporte, nombre, nombre_reportado, team, descripcion, foto_url)
    )
    hilo.start()

# =======================================================
# --- MÓDULO DE FOTOS ---
# =======================================================

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

# =======================================================
# --- RUTAS PÚBLICAS (FORMULARIOS SST) ---
# =======================================================

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
        enviar_alerta_correo(empresa, tipo_alerta, nombre_alerta, nombre_reportado, team, descripcion, url_evidencia)

        return "<h1>¡Reporte enviado con éxito!</h1>"
    except Exception as e:
        return f"Hubo un error: {str(e)}", 500

# =======================================================
# --- RUTAS PRIVADAS (DASHBOARD Y GESTIÓN SST) ---
# =======================================================

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

# =======================================================
# --- MÓDULO DE CAPACITACIONES Y EXÁMENES (SIMECAR) ---
# =======================================================

@app.route('/admin/capacitacion/nueva', methods=['GET', 'POST'])
def nueva_capacitacion():
    """Pantalla donde el Administrador crea la charla y las 4 preguntas"""
    if request.method == 'POST':
        tema = request.form.get('tema')
        tipo = request.form.get('tipo')
        lugar = request.form.get('lugar')
        fecha = request.form.get('fecha')
        hora_inicio = request.form.get('hora_inicio')
        hora_termino = request.form.get('hora_termino')
        delegacion = request.form.get('delegacion_general')
        
        preguntas = []
        for i in range(1, 5):
            pregunta = {
                "texto": request.form.get(f'q{i}_texto'),
                "opciones": {
                    "A": request.form.get(f'q{i}_a'),
                    "B": request.form.get(f'q{i}_b'),
                    "C": request.form.get(f'q{i}_c'),
                    "D": request.form.get(f'q{i}_d')
                },
                "correcta": request.form.get(f'q{i}_correcta')
            }
            preguntas.append(pregunta)
        
        nueva_charla = {
            "tema": tema,
            "tipo": tipo,
            "lugar": lugar,
            "fecha": fecha,
            "hora_inicio": hora_inicio,
            "hora_termino": hora_termino,
            "delegacion_general": delegacion,
            "datos_preguntas": json.dumps(preguntas),
            "estado": "ACTIVA"
        }
        
        respuesta = supabase.table('charlas_programadas').insert(nueva_charla).execute()
        id_charla = respuesta.data[0]['id']
        
        url_examen = f"{request.host_url}capacitacion/{id_charla}"
        return f"""
        <div style='font-family: Arial; padding: 40px; text-align: center;'>
            <h1 style='color: #2f855a;'>¡Charla y Examen Creados con Éxito! ✅</h1>
            <p>Los trabajadores deben ingresar a este link desde sus celulares para registrar asistencia y dar el examen:</p>
            <a href='{url_examen}' style='font-size: 1.2rem; font-weight: bold; color: #005eb8;'>{url_examen}</a>
            <br><br>
            <button onclick="window.location.href='/admin/dashboard'" style='padding: 10px 20px; cursor: pointer;'>Volver al Panel</button>
        </div>
        """
        
    return render_template('crear_charla.html')

@app.route('/capacitacion/<int:id_charla>', methods=['GET', 'POST'])
def rendir_evaluacion(id_charla):
    """Pantalla donde el Trabajador entra desde su celular a dar el examen"""
    charla_res = supabase.table('charlas_programadas').select('*').eq('id', id_charla).execute()
    if not charla_res.data:
        return "<h1 style='text-align:center; color:red; margin-top:50px;'>Error: La capacitación no existe o ya fue cerrada.</h1>"
    
    charla = charla_res.data[0]
    preguntas = json.loads(charla['datos_preguntas'])
    
    if request.method == 'POST':
        nombres = request.form.get('nombres')
        dni = request.form.get('dni')
        cargo = request.form.get('cargo')
        delegacion = request.form.get('delegacion')
        archivo_foto = request.files.get('evidencia')
        
        url_evidencia = subir_foto_a_supabase(archivo_foto, "SIMECAR", f"ASISTENCIA_{id_charla}") if archivo_foto else None
        
        nota = 0
        respuestas_marcadas = {}
        
        for i in range(1, 5):
            respuesta_usuario = request.form.get(f'resp_q{i}') 
            respuestas_marcadas[f'q{i}'] = respuesta_usuario
            
            if respuesta_usuario == preguntas[i-1]['correcta']:
                nota += 5
        
        nuevo_examen = {
            "id_charla": id_charla,
            "nombres": nombres.upper(),
            "dni": dni,
            "cargo": cargo.upper(),
            "delegacion": delegacion.upper(),
            "respuestas_marcadas": json.dumps(respuestas_marcadas),
            "nota_final": nota,
            "evidencia_url": url_evidencia
        }
        
        supabase.table('evaluaciones_trabajadores').insert(nuevo_examen).execute()
        
        color_nota = "#2f855a" if nota >= 15 else ("#dd6b20" if nota == 10 else "#e53e3e")
        mensaje = "¡Aprobado con honores! 🏆" if nota == 20 else ("Aprobado ✅" if nota >= 15 else "Necesitas repasar el tema ⚠️")

        return f"""
        <div style='font-family: Arial; padding: 40px; text-align: center;'>
            <h1 style='color: #005eb8;'>¡Asistencia y Examen Registrados!</h1>
            <h2 style='font-size: 3rem; color: {color_nota}; margin: 10px 0;'>Nota: {nota}/20</h2>
            <h3>{mensaje}</h3>
            <p>Ya puedes cerrar esta ventana.</p>
        </div>
        """
        
    return render_template('rendir_evaluacion.html', charla=charla, preguntas=preguntas)

@app.route('/admin/capacitacion/resultados')
def resultados_capacitaciones():
    """Panel para ver las notas de todos los trabajadores"""
    try:
        res_charlas = supabase.table('charlas_programadas').select('id, tema, fecha').execute()
        mapa_charlas = {c['id']: {'tema': c['tema'], 'fecha': c['fecha']} for c in res_charlas.data}

        res_evaluaciones = supabase.table('evaluaciones_trabajadores').select('*').order('id', desc=True).execute()
        evaluaciones = res_evaluaciones.data

        for ev in evaluaciones:
            datos_charla = mapa_charlas.get(ev.get('id_charla'), {'tema': 'Charla Desconocida', 'fecha': 'N/A'})
            ev['nombre_charla'] = datos_charla['tema']
            ev['fecha_charla'] = datos_charla['fecha']
            
            # --- MAGIA DEL TIEMPO: Capturar hora real de envío y ajustar a Perú ---
            fecha_utc_str = ev.get('created_at')
            if fecha_utc_str:
                try:
                    # Limpiamos el texto que manda Supabase (Ej: 2026-06-27T15:30:00.12345+00:00)
                    fecha_limpia = fecha_utc_str.split('.')[0].replace('T', ' ')
                    fecha_obj = datetime.strptime(fecha_limpia, '%Y-%m-%d %H:%M:%S')
                    # Ajustamos la hora al horario local (-5 horas)
                    fecha_local = fecha_obj - timedelta(hours=5)
                    ev['fecha_real_envio'] = fecha_local.strftime('%d/%m/%Y %I:%M %p')
                except:
                    ev['fecha_real_envio'] = "Desconocida"
            else:
                ev['fecha_real_envio'] = "Desconocida"
            
        return render_template('resultados_capacitaciones.html', evaluaciones=evaluaciones)
    except Exception as e:
        return f"Error al cargar los resultados: {str(e)}"

@app.route('/admin/capacitacion/exportar')
def exportar_notas_excel():
    """Descarga las notas en un archivo de Excel"""
    try:
        res_charlas = supabase.table('charlas_programadas').select('id, tema, fecha').execute()
        mapa_charlas = {c['id']: {'tema': c['tema'], 'fecha': c['fecha']} for c in res_charlas.data}
        
        res_evaluaciones = supabase.table('evaluaciones_trabajadores').select('*').order('id', desc=True).execute()
        
        datos_excel = []
        for ev in res_evaluaciones.data:
            datos_charla = mapa_charlas.get(ev.get('id_charla'), {'tema': 'Desconocida', 'fecha': 'N/A'})
            
            # Capturar hora real para el Excel
            fecha_utc_str = ev.get('created_at')
            hora_real = "Desconocida"
            if fecha_utc_str:
                try:
                    fecha_limpia = fecha_utc_str.split('.')[0].replace('T', ' ')
                    fecha_obj = datetime.strptime(fecha_limpia, '%Y-%m-%d %H:%M:%S')
                    fecha_local = fecha_obj - timedelta(hours=5)
                    hora_real = fecha_local.strftime('%d/%m/%Y %I:%M %p')
                except:
                    pass

            datos_excel.append({
                "Fecha Programada": datos_charla['fecha'],
                "Tema de Capacitación": datos_charla['tema'],
                "Trabajador": ev.get('nombres'),
                "DNI": ev.get('dni'),
                "Cargo": ev.get('cargo'),
                "Delegación": ev.get('delegacion'),
                "Nota Final (0-20)": ev.get('nota_final'),
                "Momento exacto del Examen": hora_real,  # <-- Se agrega la nueva columna al Excel
                "Evidencia (Foto)": ev.get('evidencia_url', 'Sin evidencia')
            })

        df = pd.DataFrame(datos_excel)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Notas Capacitaciones')
        
        output.seek(0)
        
        return send_file(
            output,
            as_attachment=True,
            download_name="Registro_Notas_SST_SIMECAR.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    except Exception as e:
        return f"Error al exportar a Excel: {str(e)}"

if __name__ == '__main__':
    app.run(debug=True, port=5000)
