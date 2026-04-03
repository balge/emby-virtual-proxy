# src/main.py
import uvicorn
import sys

def start_admin():
    """启动管理服务器"""
    print("--- Starting Admin Server ---")
    print("Access the Web UI at http://127.0.0.1:8011 (or your host IP)")
    print("API documentation at http://127.0.0.1:8011/docs")
    uvicorn.run("admin_server:admin_app", host="0.0.0.0", port=8001, reload=False)

def start_proxy():
    """启动代理服务器"""
    print("--- Starting Proxy Server ---")
    print("Proxy is listening on http://0.0.0.0:8999")
    uvicorn.run("proxy_server:proxy_app", host="0.0.0.0", port=8999, reload=False)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] == "admin":
            start_admin()
        elif sys.argv[1] == "proxy":
            start_proxy()
        else:
            print(f"Unknown command: {sys.argv[1]}")
            print("Available commands: admin, proxy")
    else:
        print("Please specify a service to start.")
        print("Available commands: admin, proxy")