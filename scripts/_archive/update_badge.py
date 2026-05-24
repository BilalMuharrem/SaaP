import os
import glob

old_str = """        <!-- STATUS INDICATOR -->
        <div style="margin: 0 24px 20px 24px; padding: 10px; background: rgba(52, 211, 153, 0.1); border: 1px solid rgba(52, 211, 153, 0.2); border-radius: 8px; display: flex; align-items: center; gap: 8px;" title="Arka plan motoru aktif ve izlemede">
            <style>@keyframes pulse-dot { 0% { box-shadow: 0 0 0 0 rgba(52,211,153,0.7); } 70% { box-shadow: 0 0 0 6px rgba(52,211,153,0); } 100% { box-shadow: 0 0 0 0 rgba(52,211,153,0); } }</style>
            <div style="width: 8px; height: 8px; border-radius: 50%; background: #34d399; animation: pulse-dot 2s infinite;"></div>
            <div style="font-size: 11px; font-weight: bold; color: #34d399; letter-spacing: 0.5px;">SİSTEM AKTİF</div>
        </div>"""

new_str = """        <!-- STATUS INDICATOR -->
        <div style="margin: 0 24px 20px 24px; padding: 12px; background: rgba(52, 211, 153, 0.1); border: 1px solid rgba(52, 211, 153, 0.2); border-radius: 8px; display: flex; align-items: flex-start; gap: 10px;" title="Fiyatları ve analizleri izleyen arka plan motoru devrede">
            <style>@keyframes pulse-dot { 0% { box-shadow: 0 0 0 0 rgba(52,211,153,0.7); } 70% { box-shadow: 0 0 0 6px rgba(52,211,153,0); } 100% { box-shadow: 0 0 0 0 rgba(52,211,153,0); } }</style>
            <div style="width: 8px; height: 8px; border-radius: 50%; background: #34d399; animation: pulse-dot 2s infinite; margin-top: 4px; flex-shrink: 0;"></div>
            <div>
                <div style="font-size: 11px; font-weight: 800; color: #34d399; letter-spacing: 0.5px;">SİSTEM AKTİF</div>
                <div style="font-size: 10px; color: rgba(52,211,153,0.8); margin-top: 3px; line-height: 1.3;">Arka plan fiyat izleme<br>motoru devrede.</div>
            </div>
        </div>"""

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
