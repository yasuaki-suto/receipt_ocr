import cv2
from PIL import Image
import matplotlib.pyplot as plt
import matplotlib
import base64

from pathlib import Path

from google.cloud import vision
import os
import io

from enum import Enum

import gcapi
import traceback

from flask import Flask, request, abort

from linebot import (
    LineBotApi, WebhookHandler
)

from linebot.exceptions import (
    InvalidSignatureError
)

from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage, FollowEvent,
    ImageMessage, ImageSendMessage, AudioMessage
)
from google.oauth2 import service_account

class FeatureType(Enum):
    PAGE = 1
    BLOCK = 2
    PARA = 3
    WORD = 4
    SYMBOL = 5

#環境変数取得
#LINE Developers->チャネル名->MessagingAPI設定
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('ENV_LINE_CHANNEL_ACCESS_TOKEN')
LINE_CHANNEL_SECRET       = os.getenv('ENV_LINE_CHANNEL_SECRET')
RENDER_URL = "https://receipt-ocr-ph1j.onrender.com/"
line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

app = Flask(__name__)

# 画像の保存
SRC_IMG_PATH = "static/images/{}.jpg"
def save_img(message_id, src_img_path):
    # message_idから画像のバイナリデータを取得
    message_content = line_bot_api.get_message_content(message_id)
    with open(src_img_path, "wb") as f:
        # バイナリを1024バイトずつ書き込む
        for chunk in message_content.iter_content():
            f.write(chunk)
            
def draw_boxes(input_file, bounds):
    img = cv2.imread(input_file, cv2.IMREAD_COLOR)
    for bound in bounds:
      p1 = (bound.vertices[0].x, bound.vertices[0].y) # top left
      p2 = (bound.vertices[1].x, bound.vertices[1].y) # top right
      p3 = (bound.vertices[2].x, bound.vertices[2].y) # bottom right
      p4 = (bound.vertices[3].x, bound.vertices[3].y) # bottom left
      cv2.line(img, p1, p2, (0, 255, 0), thickness=1, lineType=cv2.LINE_AA)
      cv2.line(img, p2, p3, (0, 255, 0), thickness=1, lineType=cv2.LINE_AA)
      cv2.line(img, p3, p4, (0, 255, 0), thickness=1, lineType=cv2.LINE_AA)
      cv2.line(img, p4, p1, (0, 255, 0), thickness=1, lineType=cv2.LINE_AA)
    return img

def get_document_bounds(response, feature):
    document = response.full_text_annotation
    bounds = []
    for page in document.pages:
        for block in page.blocks:
            for paragraph in block.paragraphs:
                for word in paragraph.words:
                    for symbol in word.symbols:
                        if (feature == FeatureType.SYMBOL):
                          bounds.append(symbol.bounding_box)
                    if (feature == FeatureType.WORD):
                        bounds.append(word.bounding_box)
                if (feature == FeatureType.PARA):
                    bounds.append(paragraph.bounding_box)
            if (feature == FeatureType.BLOCK):
                bounds.append(block.bounding_box)
    return bounds


def get_sorted_lines(response):
    document = response.full_text_annotation
    bounds = []
    for page in document.pages:
      for block in page.blocks:
        for paragraph in block.paragraphs:
          for word in paragraph.words:
            for symbol in word.symbols:
              x = symbol.bounding_box.vertices[0].x
              y = symbol.bounding_box.vertices[0].y
              text = symbol.text
              bounds.append([x, y, text, symbol.bounding_box])
    bounds.sort(key=lambda x: x[1])
    old_x = -1
    old_y = -1
    line = []
    lines = []
    char_width = -1
    char_height = -1
    inc = 0
    for bound in bounds:
      #print('bound')
      #print(bound)
      #print("old_y=%d" % old_y)
      x = bound[0]
      #y = bound[1]
      y = bound[3].vertices[0].y + (bound[3].vertices[2].y - bound[3].vertices[0].y) / 2 #文字の中央にする
      if char_height == -1:
          char_width = bound[3].vertices[1].x - bound[3].vertices[0].x
          char_height = bound[3].vertices[2].y - bound[3].vertices[0].y
          old_x = x
      threshold = int(char_height * 0.3) + int((x - old_x)/char_width * 0.1)
      #print("threshold=%d" % threshold)
      
      if old_y == -1:
        old_x = x
        old_y = y
      elif old_y - threshold <= y <= old_y + threshold:
        old_x = x
        old_y = y
      else:
        old_y = -1
        line.sort(key=lambda x: x[0])
        line = add_spaces(line)
        lines.append(line)
        line = []
        char_height = -1
      line.append(bound)
    line.sort(key=lambda x: x[0])
    lines.append(line)
    return lines

#渡された1行で文字間隔が広い場合、スペースで埋める
def add_spaces(line):
    last_right_top_x = -1 #行末文字の右上x座標
    char_width = -1       #１文字の幅
    char_hight = -1       #１文字の高さ
    newline = []
    #print('add_spaces')
    for bound in line:
        #print(bound[2])
        if last_right_top_x == -1:
            last_right_top_x = bound[3].vertices[1].x
            
        char_width = bound[3].vertices[1].x - bound[3].vertices[0].x
        
        # bound[0]:x座標 bound[1]:y座標 bound[2]:文字 bound[3]:vertices(=左上、右上、左下、右下のxy座標を持つ辞書)
        space_count = int((bound[3].vertices[0].x - last_right_top_x) / (char_width*1.5))
        #print('space_count=%d' % space_count)
        
        # 行末右上座標更新
        last_right_top_x = bound[3].vertices[1].x
        
        for i in range(space_count):
            offset = char_width * (i)
            space_left_top     = {'x': last_right_top_x + offset             , 'y': bound[3].vertices[0].y}
            space_left_bottom  = {'x': last_right_top_x + offset             , 'y': bound[3].vertices[2].y}
            space_right_top    = {'x': last_right_top_x + offset + char_width, 'y': bound[3].vertices[0].y}
            space_right_bottom = {'x': last_right_top_x + offset + char_width, 'y': bound[3].vertices[2].y}
            newline.append([space_left_top, space_left_bottom, ' ', {'vertices' : [space_left_top, space_left_bottom, space_right_top, space_right_bottom]}])
        newline.append(bound)
    return newline

def send_image_to_line(image_file_path):
    #バイナリデータで読み込む
    binary = open(image_file_path, mode='rb')
    #指定の辞書型にする
    image_dic = {'imageFile': binary}
    #LINEに画像とメッセージを送る
    requests.post(api_url, headers=TOKEN_dic, data=send_dic, files=image_dic)

@app.route("/")
def hello_world():
    return "hello world!"
    
@app.route("/callback", methods=['POST'])
def callback():
    # get X-Line-Signature header value
    signature = request.headers['X-Line-Signature']

    # get request body as text
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)

    # handle webhook body
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)

    return 'OK'

@handler.add(FollowEvent)
def handle_follow(event):
   line_bot_api.reply_message(
       event.reply_token,
       TextSendMessage(text='友達追加ありがとう'))
       
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=event.message.text))


@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):

    message_id = event.message.id
    src_img_path = SRC_IMG_PATH.format(message_id)   # 保存する画像のパス
    save_img(message_id, src_img_path)   # 画像を一時保存する
    input_file = src_img_path
    
    content = line_bot_api.get_message_content(event.message.id)
    content_b = b""
    for c in content.iter_content():
        content_b = content_b + c
        
    '''
    #input_file = "C:\\Users\\sutou\\Downloads\\20240223-000517.jpg"
    input_file = "C:\\Users\\sutou\\Downloads\\101774.jpg" #手動撮影未加工
    #input_file = "C:\\Users\\sutou\\Downloads\\29399.jpg"
    #input_file = "C:\\Users\\sutou\\Downloads\\DSC_0083~2.JPG" #手動撮影画像切り抜き

    '''
    img = cv2.imread(input_file)

    #with io.open(input_file, 'rb') as image_file:
    #    content = image_file.read()
    credentials = service_account.Credentials.from_service_account_file('helical-mile-415213-a2c79f1e043d.json')
    client = vision.ImageAnnotatorClient(credentials=credentials)

    image = vision.Image(content=content_b)
    response = client.text_detection(image=image)

    bounds = get_document_bounds(response, FeatureType.BLOCK)
    img_block = draw_boxes(input_file, bounds)

    bounds = get_document_bounds(response, FeatureType.PARA)
    img_para = draw_boxes(input_file, bounds)

    bounds = get_document_bounds(response, FeatureType.WORD)
    img_word = draw_boxes(input_file, bounds)

    bounds = get_document_bounds(response, FeatureType.SYMBOL)
    img_symbol = draw_boxes(input_file, bounds)

    plt.figure(figsize=[20,20])
    plt.subplot(141);plt.imshow(img_block[:,:,::-1]);plt.title("img_block")
    plt.subplot(142);plt.imshow(img_para[:,:,::-1]);plt.title("img_para")
    plt.subplot(143);plt.imshow(img_word[:,:,::-1]);plt.title("img_word")
    plt.subplot(144);plt.imshow(img_symbol[:,:,::-1]);plt.title("img_symbol")
    plt.savefig("static/images/img1.png", format='png')

    lines = get_sorted_lines(response)
    all_text=''
    for line in lines:
      texts = [i[2] for i in line]  # i[0]:x座標 i[1]:y座標 i[2]:文字 i[3]:vertices(=左上、右上、左下、右下のxy座標を持つ辞書)
      texts = ''.join(texts)
      bounds = [i[3] for i in line]
      #print(texts)
      all_text = all_text+texts + '\n'
      for bound in bounds:
        p1 = (bounds[0].vertices[0].x, bounds[0].vertices[0].y)   # top left
        p2 = (bounds[-1].vertices[1].x, bounds[-1].vertices[1].y) # top right
        p3 = (bounds[-1].vertices[2].x, bounds[-1].vertices[2].y) # bottom right
        p4 = (bounds[0].vertices[3].x, bounds[0].vertices[3].y)   # bottom left
        cv2.line(img, p1, p2, (0, 255, 0), thickness=1, lineType=cv2.LINE_AA)
        cv2.line(img, p2, p3, (0, 255, 0), thickness=1, lineType=cv2.LINE_AA)
        cv2.line(img, p3, p4, (0, 255, 0), thickness=1, lineType=cv2.LINE_AA)
        cv2.line(img, p4, p1, (0, 255, 0), thickness=1, lineType=cv2.LINE_AA)

    plt.figure(figsize=[10,10])
    plt.axis('off')
    plt.imshow(img[:,:,::-1]);plt.title("img_by_line")
    #buf = io.BytesIO()
    #plt.savefig(buf, format='png')
    #plt.show()
    #グラフ表示しない
    #plt.close()
    #tmpfile = buf.getvalue()
    #png = base64.encodebytes(buf.getvalue()).decode("utf-8")
    plt.savefig("static/images/img2.png", format='png')
    
    print(all_text)
    #print(png)
    '''
    try:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=all_text))
    except Exception as e:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="申し訳ありません。何らかのエラーが発生しました。\n %s" % traceback.format_exc()))
    '''
        
    '''
    content = line_bot_api.get_message_content(event.message.id)
    with open('./receive.jpg', 'w') as f:
        for c in content.iter_content():
            f.write(c)    
    '''
    #message_id = event.message.id
    #image_path = getImageLine(message_id)
    
    line_bot_api.reply_message(
        event.reply_token,[
        ImageSendMessage(
            original_content_url = RENDER_URL + "static/images/img1.png",
            preview_image_url = RENDER_URL + "static/images/img1.png"
        ),
        ImageSendMessage(
            original_content_url = RENDER_URL + "static/images/img2.png",
            preview_image_url = RENDER_URL + "static/images/img2.png"
        ),
        TextSendMessage(text=all_text)
        ]
    )
    
    # 一時保存していた画像を削除
    Path(SRC_IMG_PATH.format(message_id)).absolute().unlink()
    
if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
    #handle_image()
