import os
import glob

old_str = """        <!-- STATUS INDICATOR -->
        <div style="margin: 0 24px 20px 24px; padding: 12px; background: rgba(52, 211, 153, 0.1); border: 1px solid rgba(52, 211, 153, 0.2); border-radius: 8px; display: flex; align-items: flex-start; gap: 10px;" title="Fiyatları ve analizleri izleyen arka plan motoru devrede">
            <style>@keyframes pulse-dot { 0% { box-shadow: 0 0 0 0 rgba(52,211,153,0.7); } 70% { box-shadow: 0 0 0 6px rgba(52,211,153,0); } 100% { box-shadow: 0 0 0 0 rgba(52,211,153,0); } }</style>
            <div style="width: 8px; height: 8px; border-radius: 50%; background: #34d399; animation: pulse-dot 2s infinite; margin-top: 4px; flex-shrink: 0;"></div>
            <div>
                <div style="font-size: 11px; font-weight: 800; color: #34d399; letter-spacing: 0.5px;">SİSTEM AKTİF</div>
                <div style="font-size: 10px; color: rgba(52,211,153,0.8); margin-top: 3px; line-height: 1.3;">Analiz ve fiyat takip<br>motoru çalışıyor.</div>
            </div>
        </div>"""

new_str = """        <!-- STATUS INDICATOR -->
        <div id="sys-badge" style="margin: 0 24px 20px 24px; padding: 12px; background: rgba(52, 211, 153, 0.1); border: 1px solid rgba(52, 211, 153, 0.2); border-radius: 8px; display: flex; align-items: flex-start; gap: 10px; transition: all 0.3s ease;">
            <style>
            @keyframes pulse-sys-idle { 0% { box-shadow: 0 0 0 0 rgba(52,211,153,0.7); } 70% { box-shadow: 0 0 0 6px rgba(52,211,153,0); } 100% { box-shadow: 0 0 0 0 rgba(52,211,153,0); } }
            @keyframes pulse-sys-active { 0% { box-shadow: 0 0 0 0 rgba(245,158,11,0.7); } 70% { box-shadow: 0 0 0 6px rgba(245,158,11,0); } 100% { box-shadow: 0 0 0 0 rgba(245,158,11,0); } }
            </style>
            <div id="sys-dot" style="width: 8px; height: 8px; border-radius: 50%; background: #34d399; animation: pulse-sys-idle 2s infinite; margin-top: 4px; flex-shrink: 0;"></div>
            <div>
                <div id="sys-title" style="font-size: 11px; font-weight: 800; color: #34d399; letter-spacing: 0.5px;">BAĞLANIYOR...</div>
                <div id="sys-sub" style="font-size: 10px; color: rgba(255,255,255,0.6); margin-top: 3px; line-height: 1.3;">Durum kontrol ediliyor</div>
            </div>
        </div>
        <script>
        setInterval(function() {
            fetch('/api/system-status').then(r=>r.json()).then(d=>{
                const dot = document.getElementById('sys-dot');
                const title = document.getElementById('sys-title');
                const sub = document.getElementById('sys-sub');
                const badge = document.getElementById('sys-badge');
                if(!dot) return;
                if(d.is_active){
                    dot.style.background = '#f59e0b';
                    dot.style.animation = 'pulse-sys-active 1s infinite';
                    title.innerText = 'İŞLEM YAPILIYOR';
                    title.style.color = '#f59e0b';
                    sub.innerText = d.text;
                    badge.style.background = 'rgba(245,158,11,0.1)';
                    badge.style.borderColor = 'rgba(245,158,11,0.2)';
                } else {
                    dot.style.background = '#34d399';
                    dot.style.animation = 'pulse-sys-idle 3s infinite';
                    title.innerText = 'SİSTEM BEKLEMEDE';
                    title.style.color = '#34d399';
                    sub.innerText = d.text;
                    badge.style.background = 'rgba(52,211,153,0.1)';
                    badge.style.borderColor = 'rgba(52,211,153,0.2)';
                }
            }).catch(e=>{});
        }, 5000);
        </script>"""

def run():
    for f in glob.glob('/Users/mac/Desktop/BUSINESS ADVISOR/BUSINESS ADVISOR/SaaS App/templates/*.html'):
        with open(f, 'r') as file:
            c = file.read()
        if old_str in c:
            with open(f, 'w') as out:
                out.write(c.replace(old_str, new_str))
            print("REPLACED IN", f)

if __name__ == '__main__':
    run()
