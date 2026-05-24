import os
import glob

old_str = """<div style="font-size: 10px; color: rgba(52,211,153,0.8); margin-top: 3px; line-height: 1.3;">Arka plan fiyat izleme<br>motoru devrede.</div>"""
new_str = """<div style="font-size: 10px; color: rgba(52,211,153,0.8); margin-top: 3px; line-height: 1.3;">Analiz ve fiyat takip<br>motoru çalışıyor.</div>"""

def run():
    for filepath in glob.glob('/Users/mac/Desktop/BUSINESS ADVISOR/BUSINESS ADVISOR/SaaS App/templates/*.html'):
        with open(filepath, 'r') as f:
            content = f.read()
            
        if old_str in content:
            content = content.replace(old_str, new_str)
            with open(filepath, 'w') as out:
                out.write(content)
            print(f"Updated {filepath}")
            
if __name__ == '__main__':
    run()
