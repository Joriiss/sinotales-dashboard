import requests
 
API_KEY = "lgiqcp7sjy40gge00k535wp4u7c5h9d4n0lqt5kh"

# Get proxy list from WebShare API
proxy_list_response = requests.get(
    "https://proxy.webshare.io/api/v2/proxy/list/?mode=backbone&page=1&page_size=25",
    headers={"Authorization": f"Token {API_KEY}"}
)
proxy_data = proxy_list_response.json()

# Get the first proxy from the list
if proxy_data.get("results") and len(proxy_data["results"]) > 0:
    proxy = proxy_data["results"][0]
    # For backbone proxies, proxy_address is null, use p.webshare.io as default
    proxy_host = proxy.get("proxy_address") or "p.webshare.io"
    proxy_port = proxy["port"]
    username = proxy["username"]
    password = proxy["password"]
    
    proxies = {
        "http": f"http://{username}:{password}@{proxy_host}:{proxy_port}",
        "https": f"http://{username}:{password}@{proxy_host}:{proxy_port}",
    }
    
    # Get IP with proxy
    response_with_proxy = requests.get("https://httpbin.org/ip", proxies=proxies, timeout=10)
    print("IP with proxy:")
    print(response_with_proxy.json())
else:
    print("No proxies available")

# Get IP without proxy
response_without_proxy = requests.get("https://httpbin.org/ip", timeout=10)
print("\nIP without proxy:")
print(response_without_proxy.json())