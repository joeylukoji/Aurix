import logging
import os

from .service import Attestor, Config


def main():
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    att = Attestor(Config.from_env())
    att.serve_health()
    try:
        att.run_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
