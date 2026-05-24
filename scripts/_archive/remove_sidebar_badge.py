import os
import re

dir_path = "/Users/mac/Desktop/BUSINESS ADVISOR/BUSINESS ADVISOR/SaaS App/templates"
pattern = re.compile(r'<!-- Sidebar System Status Indicator -->.*?</script>', re.DOTALL)

for f in os.listdir(dir_path):
    if f.endswith('.html'):
        p = os.path.join(dir_path, f)
        with open(p, 'r') as file:
            c = file.read()
        
        nc = pattern.sub('', c)
        
        if c != nc:
            with open(p, 'w') as file:
                file.write(nc)
            print(f"Cleaned Sidebar from {f}")
