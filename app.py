import gradio as gr
import subprocess
import os
import zipfile
import re
import shutil
import glob
import traceback
import whisper

def validar_archivo(ruta):
    if ruta and os.path.exists(ruta):
        return ruta
    return None

def generar_subtitulos_karaoke(result_transcription, output_ass_path):
    with open(output_ass_path, "w", encoding="utf-8") as f:
        f.write("[Script Info]\nScriptType: v4.00+\nPlayResX: 1280\nPlayResY: 720\n\n")
        f.write("[V4+ Styles]\n")
        f.write("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n")
        f.write("Style: KaraokeStyle,Arial,48,&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,1,3,2,2,50,50,60,1\n\n")
        f.write("[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
        
        for segment in result_transcription.get('segments', []):
            start = convertir_tiempo_ass(segment['start'])
            end = convertir_tiempo_ass(segment['end'])
            
            karaoke_line = ""
            if 'words' in segment:
                for w in segment['words']:
                    duracion_cs = int((w['end'] - w['start']) * 100)
                    if duracion_cs < 1: 
                        duracion_cs = 1
                    palabra = w['word'].strip().replace(",", "").replace(".", "")
                    karaoke_line += f"{{\\kf{duracion_cs}}}{palabra} "
            else:
                karaoke_line = segment['text'].strip()
                
            f.write(f"Dialogue: 0,{start},{end},KaraokeStyle,,0,0,0,,{karaoke_line}\n")

def convertir_tiempo_ass(segundos):
    horas = int(segundos // 3600)
    minutos = int((segundos % 3600) // 60)
    secs = segundos % 60
    return f"{horas}:{minutos:02d}:{secs:02.2f}"

def separar_y_crear_karaoke(media_file, video_fondo_file, formato, progress=gr.Progress()):
    try:
        if not media_file:
            raise gr.Error("⚠️ Por favor, sube un archivo de audio o video primero.")
            
        output_dir = "resultados"
        os.makedirs(output_dir, exist_ok=True)

        # Extracción segura de la ruta del archivo principal
        if isinstance(media_file, str):
            input_path = media_file
        elif hasattr(media_file, "name"):
            input_path = media_file.name
        elif isinstance(media_file, dict) and "name" in media_file:
            input_path = media_file["name"]
        else:
            input_path = str(media_file)
        
        progress(0.02, desc="Analizando formato de archivo...")

        ext_baja = os.path.splitext(input_path)[1].lower()
        audio_para_procesar = input_path

        # Si es un video, extraemos el audio limpio a WAV para evitar conflictos
        if ext_baja in ['.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv']:
            progress(0.05, desc="Extrayendo pista de audio del archivo subido...")
            audio_extraido = os.path.join(output_dir, "audio_temporal_procesar.wav")
            cmd_extract = [
                "ffmpeg", "-y", "-i", input_path, 
                "-vn", "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "2", 
                audio_extraido
            ]
            subprocess.run(cmd_extract, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            audio_para_procesar = audio_extraido

        progress(0.10, desc="Iniciando separación de pistas con IA (Demucs)...")

        comando = ["python", "-m", "demucs", "-n", "htdemucs_6s", "-o", output_dir, audio_para_procesar]
        
        if formato == "MP3 (Comprimido)":
            comando.append("--mp3")
            ext = ".mp3"
        else:
            ext = ".wav"
            
        process = subprocess.Popen(comando, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
        for line in process.stdout:
            match = re.search(r'(\d{1,3})%', line)
            if match:
                porcentaje = int(match.group(1))
                progress(porcentaje / 100.0, desc=f"Separando instrumentos... {porcentaje}%")
        process.wait()

        # BÚSQUEDA DINÁMICA DE LA CARPETA CREADA POR DEMUCS (Evita errores por nombres largos o puntos)
        htdemucs_path = os.path.join(output_dir, "htdemucs_6s")
        if not os.path.exists(htdemucs_path):
            raise gr.Error("🚨 Demucs no generó la carpeta de resultados.")
            
        subfolders = [f.path for f in os.scandir(htdemucs_path) if f.is_dir()]
        if not subfolders:
            raise gr.Error("🚨 No se encontró la carpeta de la canción procesada.")
            
        # Selecciona la carpeta modificada más recientemente
        ruta_base = max(subfolders, key=os.path.getmtime)

        voz = validar_archivo(os.path.join(ruta_base, f"vocals{ext}"))
        piano = validar_archivo(os.path.join(ruta_base, f"piano{ext}"))
        guitarra = validar_archivo(os.path.join(ruta_base, f"guitar{ext}"))
        bateria = validar_archivo(os.path.join(ruta_base, f"drums{ext}"))
        bajo = validar_archivo(os.path.join(ruta_base, f"bass{ext}"))
        otros = validar_archivo(os.path.join(ruta_base, f"other{ext}"))

        if not all([voz, piano, guitarra, bateria, bajo, otros]):
            raise gr.Error("🚨 La IA no pudo generar todas las pistas de instrumentos.")

        progress(0.70, desc="Generando pista Instrumental...")
        instrumental = os.path.join(ruta_base, f"instrumental{ext}")
        if shutil.which("ffmpeg"):
            subprocess.run([
                "ffmpeg", "-y", "-i", piano, "-i", guitarra, "-i", bateria, "-i", bajo, "-i", otros,
                "-filter_complex", "amix=inputs=5:normalize=0", instrumental
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            instrumental = None

        # --- GENERACIÓN DE VIDEO KARAOKE PRO ---
        progress(0.80, desc="Analizando y corrigiendo letra con IA Avanzada...")
        video_path = None
        try:
            model = whisper.load_model("small")
            resultado_transcripcion = model.transcribe(voz, word_timestamps=True)
            
            ass_path = os.path.join(ruta_base, "subtitulos_karaoke.ass")
            generar_subtitulos_karaoke(resultado_transcripcion, ass_path)
            
            progress(0.92, desc="Renderizando video MP4 con efectos de letras...")
            video_path = os.path.join(ruta_base, "karaoke_profesional.mp4")
            ass_path_escaped = ass_path.replace("\\", "/").replace(":", "\\:")
            
            vid_fondo_path = None
            if video_fondo_file:
                if isinstance(video_fondo_file, str):
                    vid_fondo_path = video_fondo_file
                elif hasattr(video_fondo_file, "name"):
                    vid_fondo_path = video_fondo_file.name
                elif isinstance(video_fondo_file, dict) and "name" in video_fondo_file:
                    vid_fondo_path = video_fondo_file["name"]
                else:
                    vid_fondo_path = str(video_fondo_file)

            if vid_fondo_path and os.path.exists(vid_fondo_path):
                cmd_video = [
                    "ffmpeg", "-y",
                    "-i", vid_fondo_path,
                    "-i", instrumental,
                    "-filter_complex", f"[0:v]scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,setsar=1[vbg];[vbg]subtitles={ass_path_escaped}[v]",
                    "-map", "[v]",
                    "-map", "1:a",
                    "-c:v", "libx264", "-preset", "fast",
                    "-c:a", "aac", "-b:a", "192k",
                    "-shortest", video_path
                ]
            else:
                cmd_video = [
                    "ffmpeg", "-y",
                    "-f", "lavfi", "-i", "plasma=s=1280x720:r=30",
                    "-i", instrumental,
                    "-vf", f"subtitles={ass_path_escaped}",
                    "-c:v", "libx264", "-preset", "fast",
                    "-c:a", "aac", "-b:a", "192k",
                    "-shortest", video_path
                ]
                
            subprocess.run(cmd_video, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            if not os.path.exists(video_path) or os.path.getsize(video_path) == 0:
                video_path = None
        except Exception as ex:
            print(f"Aviso en generación de video: {ex}")
            video_path = None

        progress(0.96, desc="Comprimiendo archivo ZIP...")
        zip_path = os.path.join(ruta_base, "descarga_completa.zip")
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            if voz: zipf.write(voz, f"Acapela_Voz{ext}")
            if instrumental: zipf.write(instrumental, f"Instrumental_SinVoz{ext}")
            if video_path and os.path.exists(video_path): zipf.write(video_path, "Video_Karaoke.mp4")
            for f in [piano, guitarra, bateria, bajo, otros]:
                if f: zipf.write(f, os.path.basename(f))

        progress(1.0, desc="¡Todo listo! Karaoke profesional generado con éxito.")

        return (
            voz, piano, guitarra, bateria, bajo, otros, 
            video_path if video_path else None,
            gr.update(value=voz, interactive=True),
            gr.update(value=instrumental, interactive=True),
            gr.update(value=video_path if video_path else None, interactive=True),
            gr.update(value=zip_path, interactive=True)
        )
    except Exception as e:
        traceback.print_exc()
        raise gr.Error(f"Error detallado del sistema: {str(e)}")

def limpiar_todo():
    return (
        None, None, None, None, None, None, None, None,
        gr.update(value=None, interactive=False),
        gr.update(value=None, interactive=False),
        gr.update(value=None, interactive=False),
        gr.update(value=None, interactive=False)
    )

# ====== DISEÑO CSS PROFESIONAL ======
css_personalizado = """
body { background-color: #0b0d17 !important; color: white !important; }
.gradio-container { background-color: #0b0d17 !important; border: none !important; max-width: 95% !important; margin: 0 auto !important; }
.panel-contenedor { background-color: #131722 !important; border-radius: 12px !important; border: 1px solid #23283b !important; padding: 25px !important; }
.btn-separar { background: linear-gradient(90deg, #ff7a00, #ff0055) !important; color: white !important; font-weight: bold !important; border: none !important; padding: 15px !important; font-size: 16px !important; }
.btn-limpiar { background: transparent !important; border: 1px solid #33394f !important; color: #8892b0 !important; }
.btn-limpiar:hover { background: #23283b !important; color: white !important; }
.btn-descarga { background: transparent !important; border: 1px solid #00a8ff !important; color: #00a8ff !important; font-weight: bold !important; }
.btn-descarga:hover { background: #00a8ff !important; color: white !important; }
.titulo-header { text-align: center; margin-bottom: 0px; font-family: 'Arial Black', sans-serif; font-size: 2.8em; }
.titulo-header span.blanco { color: white; }
.titulo-header span.naranja { color: #ff7a00; }
.titulo-header span.azul { color: #00a8ff; }
.subtitulo { text-align: center; color: #8892b0; letter-spacing: 6px; font-size: 14px; margin-bottom: 30px; margin-top: 5px; }
.alerta-info { background-color: #1c1a2e !important; border: 1px solid #483475 !important; border-radius: 8px; color: #c4b5fd; text-align: center; padding: 12px; margin-bottom: 30px; }
.pista-voz { border-left: 6px solid #9d4edd !important; background-color: #1a1b26 !important; }
.pista-piano { border-left: 6px solid #2563eb !important; background-color: #1a1b26 !important; }
.pista-guitarra { border-left: 6px solid #ea580c !important; background-color: #1a1b26 !important; }
.pista-bateria { border-left: 6px solid #16a34a !important; background-color: #1a1b26 !important; }
.pista-bajo { border-left: 6px solid #db2777 !important; background-color: #1a1b26 !important; }
.pista-otros { border-left: 6px solid #0891b2 !important; background-color: #1a1b26 !important; }
.pista-karaoke { border-left: 6px solid #f59e0b !important; background-color: #1a1b26 !important; }
"""

with gr.Blocks(title="BPM PERU DJ - Universal Media & Karaoke") as interfaz:
    
    gr.HTML("""
        <div class="titulo-header">
            <span class="blanco">BPM </span><span class="naranja">PERU </span><span class="azul">DJ</span><span class="blanco"> - MEDIA STUDIO</span>
        </div>
        <div class="subtitulo">S E P A R A D O R  Y  K A R A O K E  P R O F E S I O N A L</div>
        <div class="alerta-info">ℹ️ Sube tu archivo multimedia y opcionalmente un video de fondo. Detección automática de rutas y archivos integrada.</div>
    """)
    
    with gr.Row():
        with gr.Column(scale=1, elem_classes="panel-contenedor"):
            media_input = gr.File(label="📄 SUBIR CANCIÓN O VIDEO", file_types=["audio", "video"])
            video_input = gr.File(label="🎬 VIDEO DE FONDO PERSONALIZADO (Opcional)", file_types=["video"])
            
            gr.Markdown("### ⚙️ CONFIGURACIÓN AVANZADA")
            with gr.Row():
                calidad = gr.Dropdown(choices=["Máxima (Recomendado)"], value="Máxima (Recomendado)", label="Calidad de Separación", interactive=False)
                formato = gr.Dropdown(choices=["WAV (Sin pérdida)", "MP3 (Comprimido)"], value="WAV (Sin pérdida)", label="Formato de Salida")
            
            with gr.Row():
                btn_limpiar = gr.Button("🔄 LIMPIAR", elem_classes="btn-limpiar")
                btn_separar = gr.Button("🚀 PROCESAR Y CREAR KARAOKE", elem_classes="btn-separar")
                
        with gr.Column(scale=2, elem_classes="panel-contenedor"):
            gr.Markdown("### 🎚️ PISTAS SEPARADAS Y VÍDEO KARAOKE")
            with gr.Row():
                with gr.Column(scale=1):
                    out_voz = gr.Audio(label="🎤 VOZ", elem_classes="pista-voz")
                    out_piano = gr.Audio(label="🎹 PIANO", elem_classes="pista-piano")
                    out_guitarra = gr.Audio(label="🎸 GUITARRA", elem_classes="pista-guitarra")
                
                with gr.Column(scale=1):
                    out_bateria = gr.Audio(label="🥁 BATERÍA", elem_classes="pista-bateria")
                    out_bajo = gr.Audio(label="🎸 BAJO", elem_classes="pista-bajo")
                    out_otros = gr.Audio(label="🎛️ OTROS", elem_classes="pista-otros")
            
            out_karaoke = gr.Video(label="🎬 VIDEO KARAOKE PROFESIONAL", elem_classes="pista-karaoke")
            
            gr.Markdown("### 💾 DESCARGAS RÁPIDAS")
            with gr.Row():
                btn_down_acapela = gr.DownloadButton("🎤 Acapela", interactive=False, elem_classes="btn-descarga")
                btn_down_instru = gr.DownloadButton("🎼 Instrumental", interactive=False, elem_classes="btn-descarga")
                btn_down_video = gr.DownloadButton("🎬 Video MP4", interactive=False, elem_classes="btn-descarga")
                btn_down_zip = gr.DownloadButton("📦 Todo (ZIP)", interactive=False, elem_classes="btn-descarga")
            
    gr.HTML("""
        <div style="text-align: right; margin-top: 20px; color: #8892b0; font-size: 12px;">
            © 2026 BPM PERU DJ - AUDIO<br>Todos los derechos reservados
        </div>
    """)

    btn_separar.click(
        fn=separar_y_crear_karaoke,
        inputs=[media_input, video_input, formato],
        outputs=[out_voz, out_piano, out_guitarra, out_bateria, out_bajo, out_otros, out_karaoke, btn_down_acapela, btn_down_instru, btn_down_video, btn_down_zip]
    )
    
    btn_limpiar.click(
        fn=limpiar_todo,
        inputs=None,
        outputs=[media_input, video_input, out_voz, out_piano, out_guitarra, out_bateria, out_bajo, out_otros, out_karaoke, btn_down_acapela, btn_down_instru, btn_down_video, btn_down_zip]
    )

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 7860))
    interfaz.launch(server_name="0.0.0.0", server_port=port, theme=gr.themes.Base(), css=css_personalizado)