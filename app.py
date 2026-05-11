# app.py (リセット時刻基準でデータ自動フィルタリング - 温度グラフ対応版
from flask import Flask, request, jsonify, render_template
import requests
from datetime import datetime, timezone, timedelta
import logging
import os

# ロギング設定: Render環境に合わせてカレントディレクトリにログファイルを作成
LOG_FILE_PATH = os.path.join(os.getcwd(), 'app.log') 
logging.basicConfig(filename=LOG_FILE_PATH, level=logging.INFO,
                    format='%(asctime)s %(levelname)s:%(message)s')

app = Flask(__name__)

# 機器番号とThingspeakの情報をマッピングする辞書
# 必ず YOUR_..._WRITE_API_KEY を実際の書き込みキーに置き換える。
# app.py のここを書き換える
device_mapping = {
    "device_A1": { 
        "channel_id": "3338394",       # あなたの ID にする
        "read_api_key": "LM8LHB5YH94G5YVJ", # ThingSpeakのAPI Keysタブで確認
        "write_api_key": "XDN11DIUABAHGOVZ"  # あなたの Write API Key
    }
}

@app.route('/')
def index():
    return render_template('index.html')

# =========================================================
# Field 3 リセットエンドポイント (初期設定用)
# =========================================================
@app.route('/reset_count', methods=['POST'])
def reset_count():
    device_id = request.json.get('device_id')
    
    if device_id not in device_mapping:
        logging.error(f"Invalid device ID for reset: {device_id}")
        return jsonify({"success": False, "error": "Invalid device ID"}), 404

    write_key = device_mapping[device_id].get("write_api_key")
    if not write_key:
        logging.error(f"Write API Key missing for device: {device_id}")
        return jsonify({"success": False, "error": "Write API Key not configured"}), 500

    try:
        # Field 3 (電源投入回数) を 0 に設定してThingSpeakに送信
        url = f"https://api.thingspeak.com/update?api_key={write_key}&field3=0"
        response = requests.post(url, timeout=10)
        response.raise_for_status()

        logging.info(f"Successfully sent reset signal (Field 3 = 0) for device: {device_id}")
        return jsonify({"success": True, "message": "電源投入回数を0にリセットする信号を送信しました。次回更新分からデータがフィルタされます。"}), 200

    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to reset count for device {device_id}: {e}")
        return jsonify({"success": False, "error": "ThingSpeakへの書き込みに失敗しました。"}), 500

# =========================================================
# データ取得エンドポイント (自動フィルタリングロジック)
# =========================================================
@app.route('/get_data', methods=['GET'])
def get_data():
    logging.info("Request received for get_data.")
    device_id = request.args.get('device_id', '')
    
    if device_id not in device_mapping:
        logging.error(f"Invalid device ID for get_data: {device_id}")
        return jsonify({"error": "Invalid device ID"}), 404

    device_info = device_mapping[device_id]
    channel_id = device_info["channel_id"]
    read_api_key = device_info["read_api_key"]

    try:
        # 過去のデータを多めに取得 (リセット時刻を見つけるため)
        url = f"https://api.thingspeak.com/channels/{channel_id}/feeds.json?api_key={read_api_key}&results=300" 
        
        logging.info(f"Fetching URL: {url}")
        
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        feeds = data.get('feeds', [])
        
        if not feeds:
            return jsonify({
                "temperature": "N/A", "status": "データなし", "count": "N/A",
                "graph_labels": [], "graph_data": [], "error": "データが見つかりません。"
            })

        # --- 1. Field 3 リセット後のデータを検索・フィルタリング ---
        
        reset_time_str = None
        # feedsを逆順にチェック (最新のデータから遡り、最初の '0' を見つける)
        for feed in reversed(feeds): 
            if feed.get('field3') == '0':
                reset_time_str = feed['created_at']
                break
        
        filtered_feeds = feeds
        
        if reset_time_str:
            reset_time = datetime.strptime(reset_time_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            # リセット時刻より後のデータのみを抽出
            filtered_feeds = [
                f for f in feeds 
                if datetime.strptime(f['created_at'], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) > reset_time
            ]
            logging.info(f"Data filtered using reset time: {reset_time_str}. {len(filtered_feeds)} records remaining.")
        
        if not filtered_feeds:
             # リセット後にまだ新しいデータが来ていない場合
             return jsonify({
                "temperature": "N/A", "status": "待機中", "count": "0",
                "graph_labels": [], "graph_data": [], "error": "リセット後の新しいデータ待ちです。"
            })


        # --- 2. データの抽出と処理 ---
        latest_feed = filtered_feeds[-1] # フィルタリング後の最新データ
        
        graph_labels = []
        graph_temp_data = [] # 温度データ用に変数名を変更

        for f in filtered_feeds:
            # Field 1 (温度) のデータが存在する場合、グラフデータとして抽出
            if f.get('field1') is not None:
                utc_time = datetime.strptime(f['created_at'], "%Y-%m-%dT%H:%M:%SZ")
                graph_labels.append(utc_time.strftime("%H:%M")) 
                try:
                    # 温度は float に変換
                    graph_temp_data.append(float(f['field1']))
                except ValueError:
                    # データが無効な場合はスキップ
                    pass 
        
        current_status = latest_feed.get('field2', '0')
        status_text = '稼働中' if current_status == '1' else '停止中'
        
        latest_temperature = latest_feed.get('field1', 'N/A') # 最新の温度値
        
        logging.info(f"Data processed successfully for device {device_id}. Status: {status_text}, Temp records: {len(graph_temp_data)}")
        return jsonify({
            "temperature": latest_temperature, # 最新の温度値を返す
            "status": status_text,
            "count": latest_feed.get('field3', 'N/A'),
            "graph_labels": graph_labels,
            "graph_data": graph_temp_data # 温度のグラフデータを返す
        })

    except requests.exceptions.RequestException as e:
        logging.error(f"ThingSpeak API communication failed for device {device_id}: {e}")
        return jsonify({
            "error": "ThingSpeakとの通信に失敗しました。",
            "temperature": "通信エラー",
            "status": "接続不良",
            "count": "N/A",
            "graph_labels": [],
            "graph_data": []
        }), 500

if __name__ == '__main__':
    pass
