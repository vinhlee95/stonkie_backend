import asyncio

from dotenv import load_dotenv

from services.company_insight import fetch_unsplash_image

load_dotenv()


async def main():
    # Test with a known ticker
    ticker = "AAPL"  # Apple
    print(f"Testing fetch_unsplash_image for {ticker}")
    result = await fetch_unsplash_image(ticker)
    print(f"Result: {result}")


if __name__ == "__main__":
    asyncio.run(main())
