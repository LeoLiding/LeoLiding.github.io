import threading
import time
import base64
import mimetypes
import os
from openai import OpenAI
import tiktoken  # 用于计算 token 数量
import msvcrt  # Windows 专用
import sounddevice as sd
from scipy.io.wavfile import write, read
from datetime import datetime
import json
import uuid
import traceback
import numpy as np
from pydub import AudioSegment  # 添加音频处理库

# 创建必要的文件夹
def ensure_directories():
    # 获取当前脚本所在目录
    current_dir = os.path.dirname(os.path.abspath(__file__))
    # 创建 audio 和 log 文件夹
    audio_dir = os.path.join(current_dir, "audio")
    log_dir = os.path.join(current_dir, "log")
    os.makedirs(audio_dir, exist_ok=True)
    os.makedirs(log_dir, exist_ok=True)
    return audio_dir, log_dir

# 初始化文件夹
AUDIO_DIR, LOG_DIR = ensure_directories()

# 初始化客户端
client = OpenAI(
    api_key="token-abc123",
    base_url="http://183.11.229.111:8048/v1"
)

# 打断事件监听
stop_event = threading.Event()

def compress_audio(input_path, max_size_mb=30):
    """压缩音频文件，确保大小不超过指定限制"""
    try:
        audio = AudioSegment.from_wav(input_path)
        original_size = os.path.getsize(input_path) / (1024 * 1024)  # 转换为MB
        
        if original_size <= max_size_mb:
            return input_path
            
        # 计算需要的压缩比例
        compression_ratio = max_size_mb / original_size
        
        # 降低采样率（最小到8kHz）
        target_sample_rate = max(8000, int(audio.frame_rate * compression_ratio))
        audio = audio.set_frame_rate(target_sample_rate)
        
        # 转换为单声道
        audio = audio.set_channels(1)
        
        # 保存压缩后的音频
        compressed_path = input_path.replace('.wav', '_compressed.wav')
        audio.export(compressed_path, format='wav')
        
        compressed_size = os.path.getsize(compressed_path) / (1024 * 1024)
        print(f"📊 音频压缩：{original_size:.1f}MB -> {compressed_size:.1f}MB")
        
        return compressed_path
    except Exception as e:
        print(f"⚠️ 音频压缩失败：{str(e)}")
        return input_path

# 录音函数
def record_audio(filename=None, fs=16000):
    if filename is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"recorded_audio.wav"
    # 确保文件名在 audio 目录下
    filepath = os.path.join(AUDIO_DIR, filename)
    
    print(f"🎙️ 按住空格键开始录音，松开结束录音...")
    print(f"⚠️ 注意：录音文件大小请不要超过30MB")
    
    # 等待空格键按下
    while True:
        if msvcrt.kbhit():
            key = msvcrt.getch()
            if key == b' ':  # 空格键
                break
        time.sleep(0.1)
    
    # 开始录音
    recording = []
    print("🎙️ 开始录音...")
    
    # 创建录音流
    with sd.InputStream(samplerate=fs, channels=1, dtype='int16') as stream:
        while True:
            if msvcrt.kbhit():
                key = msvcrt.getch()
                if key == b' ':  # 如果空格键被松开
                    break
            audio_chunk, _ = stream.read(1024)
            recording.append(audio_chunk)
    
    # 合并录音数据
    recording = np.concatenate(recording, axis=0)
    
    # 保存录音
    write(filepath, fs, recording)
    
    # 压缩音频
    compressed_path = compress_audio(filepath)
    print(f"✅ 录音完成，保存为：{compressed_path}")
    return compressed_path

# 打断监听器
def interrupt_listener():
    print("\n⏳ 提示：按任意键打断输出...\n")
    while not stop_event.is_set():
        if msvcrt.kbhit():
            msvcrt.getch()
            stop_event.set()
            print("\n🛑 手动打断响应！\n")
            break
        time.sleep(0.1)

# 文件转 base64 + MIME 类型
def file_to_data_url(file_path):
    with open(file_path, 'rb') as f:
        base64_data = base64.b64encode(f.read()).decode('utf-8')
    mime_type, _ = mimetypes.guess_type(file_path)
    mime_type = mime_type or "application/octet-stream"
    return f"data:{mime_type};base64,{base64_data}"

# 初始化对话历史
messages = [
    {
        "role": "system",
        "content": """你是一个和我对话的人工智能助手，请使用温暖、亲切的语气和我交流，帮我疏解我的心理。
"""
            }
        ]
#"content": "你是一个和我对话的人工智能助手，请使用温暖、亲切的语气和我交流，帮我疏解我的心理。并在每次输出时，将我输入的音频转成文本一起输出。"
#统计 messages 的总 token 数
def count_tokens(messages, model="gpt-3.5-turbo"):  # 兼容 OpenAI 格式
    enc = tiktoken.encoding_for_model(model)
    tokens_per_message = 3  # 每条消息的额外token（OpenAI假设）
    tokens_per_name = 1     # 如果存在name字段

    num_tokens = 0
    for message in messages:
        num_tokens += tokens_per_message
        for key, value in message.items():
            if key == "content":
                if isinstance(value, list):  # 支持音频结构
                    for item in value:
                        if item["type"] == "text":
                            num_tokens += len(enc.encode(item["text"]))
                        elif item["type"] == "audio_url":
                            # 音频结构不算入 token，或略估一个长度
                            num_tokens += 5
                elif isinstance(value, str):
                    num_tokens += len(enc.encode(value))
            elif key == "role":
                num_tokens += len(enc.encode(value))
            elif key == "name":
                num_tokens += tokens_per_name + len(enc.encode(value))
    return num_tokens + 3  # 最后的 reply 消息的 priming tokens

# 保存对话历史到文件
def save_conversation_history(messages, filename=None):
    if filename is None:
        # 使用当前时间创建文件名
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"conversation_history_{timestamp}.txt"
    
    # 确保文件名在 log 目录下
    filepath = os.path.join(LOG_DIR, filename)
    
    with open(filepath, "w", encoding="utf-8") as f:
        # 将messages列表转换为JSON字符串，确保中文正确显示
        json_str = json.dumps(messages, ensure_ascii=False, indent=2)
        f.write(json_str)
    print(f"\n💾 对话历史已保存到：{filepath}")
    return filepath

# 主流程（支持多轮）
def run_minicpm_stream():
    round_count = 1
    conversation_file = None
    while True:
        print(f"\n🗣️ 第 {round_count} 轮对话")

        # 打印历史对话记录
        print("\n📜 历史消息队列：")
        for i, msg in enumerate(messages):
            role = "👤 用户     " if msg["role"] == "user" else ("🤖 Minicpm-o" if msg["role"] == "assistant" else "⚙️  系统     ")
            content = msg["content"]
            if isinstance(content, list):
                content_str = ""
                for item in content:
                    if item["type"] == "text":
                        content_str += item["text"]
                    elif item["type"] == "audio_url":
                        raw = str(item)[:100].replace("\n", "")
                        content_str += f"[🎵 音频结构前100字: {raw}...]"
                print(f"{role}：{content_str}")
            else:
                print(f"{role}：{content}")

        # 输入文本内容
        user_text = input("\n请输入文本内容（exit 退出）：").strip()
        if user_text.lower() == "exit":
            break

        content_list = [{"type": "text", "text": user_text}]

        # 是否录音
        use_recording = input("是否进行录音？(y/N)：").strip().lower()
        if use_recording == "y":
            audio_path = record_audio()
            audio_data_url = file_to_data_url(audio_path)
            content_list.append({
                "type": "audio_url",
                "audio_url": {"url": audio_data_url}
            })
        else:
            audio_path = input("请输入音频文件路径（留空跳过，exit 退出）：").strip()
            if audio_path.lower() == "exit":
                return
            if audio_path:
                if os.path.exists(audio_path):
                    audio_data_url = file_to_data_url(audio_path)
                    content_list.append({
                        "type": "audio_url",
                        "audio_url": {"url": audio_data_url}
                    })
                else:
                    print("❌ 音频文件不存在，跳过本轮。")
                    continue


        # 加入对话内容
        messages.append({
            "role": "user",
            "content": content_list
        })
        # 每轮启动新的打断监听器线程
        stop_event.clear()
        t_interrupt = threading.Thread(target=interrupt_listener)
        t_interrupt.start()

        # 请求响应
        t1 = time.time()
        response = client.chat.completions.create(
            model="/workspace/minicpm",
            messages=messages,
            extra_body={
                "stop_token_ids": [151645, 151643],
                "temperature": 0.7,
                "top_p": 0.7
            },
            max_tokens=1024,
            stream=True
        )

        # 输出响应
        first_token_time = None
        full_reply = ""

        for chunk in response:
            if stop_event.is_set():
                break
            if chunk.choices[0].delta and chunk.choices[0].delta.content:
                if first_token_time is None:
                    first_token_time = time.time()
                    print(f"\n⏱️ 首个响应用时: {first_token_time - t1:.3f} 秒\n")
                content_piece = chunk.choices[0].delta.content
                print(content_piece, end="", flush=True)
                full_reply += content_piece

        print("\n")
        
        # 添加：结束监听线程
        stop_event.set()
        t_interrupt.join()

        messages.append({
            "role": "assistant",
            "content": full_reply
        })

        # ✅ 打印当前对话占用的 token 数量
        token_count = count_tokens(messages, model="gpt-3.5-turbo")
        print(f"🧮 当前总 token 数: {1.6 * token_count:.1f} tokens (粗略估算)")

        # 保存对话历史
        if conversation_file is None:
            conversation_file = save_conversation_history(messages)
        else:
            save_conversation_history(messages, conversation_file)

        round_count += 1
        stop_event.clear()


# 启动线程
t_stream = threading.Thread(target=run_minicpm_stream)
# t_interrupt = threading.Thread(target=interrupt_listener)

t_stream.start()
# t_interrupt.start()

t_stream.join()
stop_event.set()  # 保证监听线程能退出
