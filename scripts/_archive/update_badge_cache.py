import os
import glob

old_str = "fetch('/api/system-status')"
new_str = "fetch('/api/system-status?_t=' + new Date().getTime())"

def run():
    for f in glob.glob('/Users/mac/Desktop/BUSINESS ADVISOR/BUSINESS ADVISOR/SaaS App/templates/*.html'):
        with open(f, 'r') as file:
            c = file.read()
        if old_str in c:
            with open(f, 'w') as out:
                out.write(c.replace(old_str, new_str))
            print("CACHE BUSTER ADDED IN", f)

if __name__ == '__main__':
    run()
