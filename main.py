from web3 import Web3
import json
import os
import asyncio
from web3 import AsyncWeb3, WebSocketProvider
from eth_abi.abi import decode
from dotenv import load_dotenv

load_dotenv()

# Connect to the Sepolia testnet
rpc_url = os.getenv("RPC_URL")
if rpc_url is None:
    raise Exception("RPC_URL environment variable not set")

pk = os.environ.get('PRIVATE_KEY')



# Smart contract address and ABI
contract_address = "0x1c6AbAaf5b8a410Ae89d30C84a0123173DaabfA3"

market_details_abi = json.loads('''
  [  {
    "type": "function",
    "name": "getDeployedMarket",
    "inputs": [
      { "name": "_marketId", "type": "uint256", "internalType": "uint256" }
    ],
    "outputs": [
      { "name": "", "type": "address", "internalType": "address" },
      { "name": "", "type": "uint256", "internalType": "uint256" },
      { "name": "", "type": "uint256", "internalType": "uint256" },
      { "name": "", "type": "string", "internalType": "string" },
      { "name": "", "type": "string", "internalType": "string" },
      { "name": "", "type": "uint256", "internalType": "uint256" },
      { "name": "", "type": "uint256", "internalType": "uint256" },
      { "name": "", "type": "address", "internalType": "address" },
      { "name": "", "type": "bool", "internalType": "bool" },
      { "name": "", "type": "uint256", "internalType": "uint256" },
      {
        "name": "",
        "type": "uint8",
        "internalType": "enum Market.outcomeType"
      },
      { "name": "", "type": "address", "internalType": "address" },
      { "name": "", "type": "address", "internalType": "address" }
    ],
    "stateMutability": "view"
  },
  {
    "type": "function",
    "name": "markets",
    "inputs": [{ "name": "", "type": "uint256", "internalType": "uint256" }],
    "outputs": [
      { "name": "", "type": "address", "internalType": "contract Market" }
    ],
    "stateMutability": "view"
  }]''')


resolve_abi = json.loads('''
[{
      "type": "function",
      "name": "resolve",
      "inputs": [
        {
          "name": "_finalResolution",
          "type": "uint8",
          "internalType": "enum Market.outcomeType"
        }
      ],
      "outputs": [],
      "stateMutability": "nonpayable"
},
{
  "type": "function",
  "name": "distribute",
  "inputs": [],
  "outputs": [],
  "stateMutability": "nonpayable"
}
]''')

async def subscribe_to_transfer_events():
    async with AsyncWeb3(WebSocketProvider(rpc_url)) as w3:
        market_created_event_topic = w3.keccak(text="MarketCreated(uint256,string,string,uint256,uint256,address)")
        filter_params = {
            "address": contract_address,
            "topics": [market_created_event_topic],
        }
        subscription_id = await w3.eth.subscribe("logs", filter_params)
        print(f"Subscribing to transfer events for FourMarket at {subscription_id}")

        async for payload in w3.socket.process_subscriptions():
            result = payload["result"]

            # Decode the event data
            topics = result["topics"]
            data = result["data"]

            market_id = decode(["uint256"], topics[1])[0]

            print(f"Market created with ID {market_id}")

            # Create contract instance
            contract = w3.eth.contract(address=contract_address, abi=market_details_abi)

            # Call getDeployedMarket with the market_id
            deployed_market = await contract.functions.getDeployedMarket(market_id).call()

            # Call markets with the market_id to get the market address
            market_contract_address = await contract.functions.markets(market_id).call()

            print(f"Deployed Market: {deployed_market}")
            print(f"Market Contract Address: {market_contract_address}")

            deadline = deployed_market[5]

            # Set a timeout till the deadline
            latest_block = await w3.eth.get_block('latest')
            
            timeout = deadline - latest_block['timestamp']
            print(f"Will wait for {timeout} seconds. Deadline is {deadline}. Current block timestamp is {latest_block['timestamp']}")
            if timeout > 0:
                await asyncio.sleep(timeout)

            else:
                print(f"Deadline already passed for market ID {market_id}")

            print(f"Timeout reached for market ID {market_id}")
            print(f"Starting resolution for market ID {market_id}")

            from openai import OpenAI

            client = OpenAI(
                api_key=os.environ.get('OPENAI_API_KEY'),
                base_url="https://llm-gateway.heurist.xyz"
            )

            completion = client.chat.completions.create(
                model="mistralai/mixtral-8x7b-instruct",
                messages=[
                    {
                        "role": "system",
                        "content": f"""
                    You are a keeper for a prediction market. You are given a question and details about the market.
                    You must answer the question, and precisely return either "yes" or "no" as the outcome. You must only return "yes" or "no" - else the program will fail.
                    """,
                    },
                    {
                        "role": "user",
                        "content": deployed_market[3],
                    }
                ],
                max_tokens=1,
                stream=False
            )

            result = completion.choices[0].message.content.lower()

            print(f"Resolution for {market_id} with question {deployed_market[3]}: {result}")
            
            resolution = 1

            if result == "yes":
                resolution = 1
            elif result == "no":
                resolution = 2

            keeper = w3.eth.account.from_key(pk)

            nonce = await w3.eth.get_transaction_count(keeper.address)

            chain_id = await w3.eth.chain_id
                                        
            market_contract = w3.eth.contract(address=market_contract_address, abi=resolve_abi)
            tx = await market_contract.functions.resolve(resolution).build_transaction({"chainId": chain_id, "from": keeper.address, "nonce": nonce})
            signed_tx = w3.eth.account.sign_transaction(tx, private_key=pk)
            tx_hash = await w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            print(f"Market resolved with transaction hash: {tx_hash.hex()}")

asyncio.run(subscribe_to_transfer_events())