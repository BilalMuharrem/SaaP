from app import app, db
from models import Job
import re

with app.app_context():
    jobs = Job.query.filter(Job.result_html != None).all()
    updated = 0
    
    style_replacement = """<style>
:root {
    --bg: #09090b; --text: #f8fafc; --card: rgba(24,24,27,0.55); --border: rgba(255,255,255,0.08);
    --ai-bg: rgba(30,41,59,0.5); --ai-border: rgba(255,255,255,0.05); --light-border: rgba(255,255,255,0.06);
    --grad: linear-gradient(135deg, rgba(79,70,229,0.15) 0%, rgba(139,92,246,0.15) 100%);
    --muted: #a1a1aa; --link-bg: rgba(255,255,255,0.05); --title: #818cf8; --report-color: #52525b;
}
[data-theme="light"] {
    --bg: #ffffff; --text: #0f172a; --card: #f8fafc; --border: rgba(0,0,0,0.1);
    --ai-bg: #f8fafc; --ai-border: rgba(0,0,0,0.05); --light-border: rgba(0,0,0,0.05);
    --grad: linear-gradient(135deg, #f8fafc 0%, #f1f5f9 100%);
    --muted: #64748b; --link-bg: rgba(0,0,0,0.05); --title: #4f46e5; --report-color: #94a3b8;
}
body{font-family:'Plus Jakarta Sans',sans-serif;background:var(--bg);color:var(--text);padding:40px;margin:0;}
.container{max-width:1100px;margin:auto;}
.section-divider{border:none;border-top:1px solid var(--border);margin:40px 0;}
a { color: var(--text) !important; }
</style>
<script>
function sync(){try{let t=window.parent.document.documentElement.getAttribute('data-theme');if(t)document.documentElement.setAttribute('data-theme',t);}catch(e){}}
sync(); window.addEventListener('message',e=>{if(e.data&&e.data.theme)document.documentElement.setAttribute('data-theme',e.data.theme);});
setInterval(sync, 1000);
</script>"""

    for job in jobs:
        text = job.result_html
        original = text
        
        # 1. Replace HTML Heads
        text = re.sub(r'<style>body{font-family:\'Plus Jakarta Sans\',sans-serif;background:#09090b.*?</style>', style_replacement, text, flags=re.DOTALL)
        
        # 2. Replace hardcoded RGBAs inside the saved HTML payload
        text = text.replace("background:rgba(30,41,59,0.7)", "background:var(--card)")
        text = text.replace("border:1px solid rgba(255,255,255,0.05)", "border:1px solid var(--ai-border)")
        text = text.replace("background:rgba(255,255,255,0.05)", "background:var(--link-bg)")
        
        text = text.replace("color:#a1a1aa", "color:var(--muted)")
        text = text.replace("color:#52525b", "color:var(--report-color)")
        text = text.replace("color:#d1d5db", "color:var(--text)")
        text = text.replace("color:#e4e4e7", "color:var(--text)")
        text = text.replace("color:#e2e8f0", "color:var(--text)")
        text = text.replace("color:#ddd6fe", "color:var(--title)")
        text = text.replace("color:#818cf8", "color:var(--title)")
        text = text.replace("color:#fff;", "color:var(--text);")
        text = text.replace("color:#ffffff", "color:var(--text)")
        
        text = text.replace("background:linear-gradient(135deg, rgba(79,70,229,0.15) 0%, rgba(139,92,246,0.15) 100%)", "background:var(--grad)")
        text = text.replace("border:1px solid rgba(139,92,246,0.3)", "border:1px solid var(--border)")
        
        text = text.replace("background:rgba(24,24,27,0.55)", "background:var(--card)")
        text = text.replace("border:1px solid rgba(255,255,255,0.08)", "border:1px solid var(--border)")
        text = text.replace("border-right:1px solid rgba(255,255,255,0.06)", "border-right:1px solid var(--light-border)")
        text = text.replace("background:rgba(129,140,248,0.1)", "background:var(--link-bg)")
        text = text.replace("background:rgba(30,41,59,0.5)", "background:var(--ai-bg)")
        
        if text != original:
            job.result_html = text
            updated += 1
            
    db.session.commit()
    print(f"Migration Complete: Upgraded {updated} legacy reports to Light Mode compatibility.")
