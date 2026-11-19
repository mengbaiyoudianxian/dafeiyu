import twikit, os, shutil, time
p = os.path.join(os.path.dirname(twikit.__file__), "client", "client.py")
s = open(p, encoding="utf-8").read()
old = """            await self.client_transaction.init(self.http, ct_headers)
            self.set_cookies(cookies_backup, clear_cookies=True)

        tid = self.client_transaction.generate_transaction_id(method=method, path=urlparse(url).path)
        headers['X-Client-Transaction-Id'] = tid
"""
new = """            try:  # dafeiyu: X 换了新前端，签名数据找不到时不签名照常请求
                await self.client_transaction.init(self.http, ct_headers)
            except Exception:
                self.client_transaction.home_page_response = True
                self.client_transaction.dafeiyu_broken = True
            self.set_cookies(cookies_backup, clear_cookies=True)

        if not getattr(self.client_transaction, 'dafeiyu_broken', False):
            try:
                tid = self.client_transaction.generate_transaction_id(method=method, path=urlparse(url).path)
                headers['X-Client-Transaction-Id'] = tid
            except Exception:
                pass
"""
if "dafeiyu:" in s:
    print("already patched")
else:
    assert old in s, "anchor not found"
    shutil.copy2(p, f"/opt/dafeiyu/downloads/twikit-client.py.orig-{int(time.time())}")
    open(p, "w", encoding="utf-8").write(s.replace(old, new))
    print("patched")
