// aurixd is a single-validator AURIX node exposing a JSON RPC over HTTP.
package main

import (
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"log"
	"net/http"
	"os"
	"strconv"
	"sync"
	"time"

	"github.com/joeylukoji/aurix/chain/ledger"
)

type pending struct {
	tx   ledger.Tx
	done chan ledger.TxResult
}

type node struct {
	state   *ledger.State
	mu      sync.Mutex
	mempool []pending
	txIndex sync.Map // hash -> TxResult
}

func seedKey(seed string) ed25519.PrivateKey {
	h := sha256.Sum256([]byte(seed))
	return ed25519.NewKeyFromSeed(h[:])
}

func env(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

// produce commits the mempool into a block at every tick (empty blocks too,
// so that escrow deadlines expressed in block height advance).
func (n *node) produce(interval time.Duration) {
	for range time.Tick(interval) {
		n.mu.Lock()
		batch := n.mempool
		n.mempool = nil
		n.mu.Unlock()
		txs := make([]ledger.Tx, len(batch))
		for i, p := range batch {
			txs[i] = p.tx
		}
		b := n.state.CommitBlock(txs, time.Now().Unix())
		for i, r := range b.Txs {
			n.txIndex.Store(r.Hash, r)
			batch[i].done <- r
		}
	}
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(v)
}

func errJSON(w http.ResponseWriter, code int, err error) {
	writeJSON(w, code, map[string]any{"error": err.Error()})
}

func (n *node) routes() *http.ServeMux {
	m := http.NewServeMux()
	m.HandleFunc("GET /status", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, map[string]any{"chain_id": n.state.ChainID, "height": n.state.Height(),
			"total_supply": n.state.TotalSupply(), "denom": "uaur"})
	})
	m.HandleFunc("GET /accounts/{addr}", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, n.state.Account(r.PathValue("addr")))
	})
	m.HandleFunc("POST /txs", func(w http.ResponseWriter, r *http.Request) {
		var tx ledger.Tx
		if err := json.NewDecoder(r.Body).Decode(&tx); err != nil {
			errJSON(w, 400, err)
			return
		}
		if err := n.state.CheckTx(tx); err != nil { // CheckTx: reject early
			errJSON(w, 400, err)
			return
		}
		p := pending{tx, make(chan ledger.TxResult, 1)}
		n.mu.Lock()
		n.mempool = append(n.mempool, p)
		n.mu.Unlock()
		select {
		case res := <-p.done:
			code := 200
			if res.Code != 0 {
				code = 400
			}
			writeJSON(w, code, res)
		case <-time.After(15 * time.Second):
			errJSON(w, 504, errors.New("timeout waiting for block"))
		}
	})
	m.HandleFunc("GET /txs/{hash}", func(w http.ResponseWriter, r *http.Request) {
		if v, ok := n.txIndex.Load(r.PathValue("hash")); ok {
			writeJSON(w, 200, v)
			return
		}
		errJSON(w, 404, errors.New("tx not found"))
	})
	m.HandleFunc("GET /blocks", func(w http.ResponseWriter, r *http.Request) {
		limit, _ := strconv.Atoi(r.URL.Query().Get("limit"))
		if limit <= 0 || limit > 500 {
			limit = 50
		}
		writeJSON(w, 200, n.state.Blocks(limit))
	})
	m.HandleFunc("GET /blocks/{h}", func(w http.ResponseWriter, r *http.Request) {
		h, _ := strconv.ParseInt(r.PathValue("h"), 10, 64)
		if b, ok := n.state.Block(h); ok {
			writeJSON(w, 200, b)
			return
		}
		errJSON(w, 404, errors.New("block not found"))
	})
	m.HandleFunc("GET /escrows", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, n.state.Escrows())
	})
	m.HandleFunc("GET /escrows/{id}", func(w http.ResponseWriter, r *http.Request) {
		id, _ := strconv.ParseUint(r.PathValue("id"), 10, 64)
		if e, ok := n.state.Escrow(id); ok {
			writeJSON(w, 200, e)
			return
		}
		errJSON(w, 404, errors.New("escrow not found"))
	})
	m.HandleFunc("GET /proofs/{hash}", func(w http.ResponseWriter, r *http.Request) {
		if p, ok := n.state.Proof(r.PathValue("hash")); ok {
			writeJSON(w, 200, p)
			return
		}
		errJSON(w, 404, errors.New("proof not found"))
	})
	return m
}

func main() {
	faucet := seedKey(env("AURIX_FAUCET_SEED", "aurix-dev-faucet"))
	gateway := seedKey(env("AURIX_GATEWAY_SEED", "aurix-dev-gateway"))
	addr := func(k ed25519.PrivateKey) string { return ledger.AddressFromPubKey(k.Public().(ed25519.PublicKey)) }
	g := ledger.Genesis{
		ChainID: env("AURIX_CHAIN_ID", "aurix-testnet-1"),
		Balances: map[string]uint64{
			addr(faucet):  1_000_000 * ledger.MicroPerAUR,
			addr(gateway): 1_000 * ledger.MicroPerAUR,
		},
		AnchorSigners: []string{addr(gateway)},
	}
	interval, _ := time.ParseDuration(env("AURIX_BLOCK_INTERVAL", "1s"))
	n := &node{state: ledger.NewState(g)}
	go n.produce(interval)
	listen := env("AURIX_LISTEN", ":26657")
	log.Printf("aurixd chain=%s faucet=%s gateway=%s listening on %s", g.ChainID, addr(faucet), addr(gateway), listen)
	log.Fatal(http.ListenAndServe(listen, n.routes()))
}
