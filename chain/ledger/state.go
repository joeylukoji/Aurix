package ledger

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"strings"
	"sync"
)

type Genesis struct {
	ChainID        string            `json:"chain_id"`
	Balances       map[string]uint64 `json:"balances"`
	AnchorSigners  []string          `json:"anchor_signers"` // gateway addresses allowed to anchor proofs
	FeeCollector   string            `json:"fee_collector"`
}

// State is the full ledger state. All mutations go through DeliverTx under mu.
type State struct {
	mu            sync.RWMutex
	ChainID       string
	accounts      map[string]*Account
	escrows       map[uint64]*Escrow
	proofs        map[string]*PaymentProof
	nextEscrowID  uint64
	height        int64
	blocks        []Block
	totalSupply   uint64
	anchorSigners map[string]bool
	feeCollector  string
}

func NewState(g Genesis) *State {
	s := &State{
		ChainID:       g.ChainID,
		accounts:      map[string]*Account{},
		escrows:       map[uint64]*Escrow{},
		proofs:        map[string]*PaymentProof{},
		nextEscrowID:  1,
		anchorSigners: map[string]bool{},
		feeCollector:  g.FeeCollector,
	}
	if s.feeCollector == "" {
		s.feeCollector = "aur1feecollector"
	}
	for addr, bal := range g.Balances {
		s.acct(addr).Balance = bal
		s.totalSupply += bal
	}
	for _, a := range g.AnchorSigners {
		s.anchorSigners[a] = true
	}
	s.blocks = append(s.blocks, Block{Height: 0, Hash: strings.Repeat("0", 64), Txs: []TxResult{}})
	return s
}

func (s *State) acct(addr string) *Account {
	a, ok := s.accounts[addr]
	if !ok {
		a = &Account{Address: addr}
		s.accounts[addr] = a
	}
	return a
}

var (
	ErrBadNonce     = errors.New("invalid nonce (replay or out of order)")
	ErrInsufficient = errors.New("insufficient funds")
)

// CheckTx validates a tx against current state without mutating it.
func (s *State) CheckTx(tx Tx) error {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.check(tx)
}

func (s *State) check(tx Tx) error {
	if err := tx.VerifySignature(s.ChainID); err != nil {
		return err
	}
	if tx.Fee < DefaultFee {
		return fmt.Errorf("fee below minimum %d uaur", DefaultFee)
	}
	a := s.accounts[tx.From]
	var nonce, bal uint64
	if a != nil {
		nonce, bal = a.Nonce, a.Balance
	}
	if tx.Nonce != nonce {
		return ErrBadNonce
	}
	need := tx.Fee
	m := tx.Msg
	switch m.Type {
	case TypeSend:
		if m.Amount == 0 || !validAddr(m.To) {
			return errors.New("invalid amount or recipient")
		}
		need += m.Amount
	case TypeCreateEscrow:
		if m.Amount == 0 || !validAddr(m.To) {
			return errors.New("invalid amount or recipient")
		}
		if m.Arbiter != "" && !validAddr(m.Arbiter) {
			return errors.New("invalid arbiter")
		}
		if m.DeadlineHeight <= s.height {
			return errors.New("deadline must be above current height")
		}
		need += m.Amount
	case TypeReleaseEscrow, TypeRefundEscrow:
		e := s.escrows[m.EscrowID]
		if e == nil {
			return errors.New("escrow not found")
		}
		if e.Status != Locked {
			return fmt.Errorf("escrow is %s, not LOCKED", e.Status)
		}
		if m.Type == TypeReleaseEscrow && tx.From != e.Sender && tx.From != e.Arbiter {
			return errors.New("only sender or arbiter can release")
		}
		if m.Type == TypeRefundEscrow {
			if tx.From != e.Sender && tx.From != e.Arbiter {
				return errors.New("only sender or arbiter can refund")
			}
			// the sender may only refund after the deadline; the arbiter may cancel anytime
			if tx.From == e.Sender && tx.From != e.Arbiter && s.height < e.DeadlineHeight {
				return errors.New("deadline not reached")
			}
		}
	case TypeAnchorProof:
		if !s.anchorSigners[tx.From] {
			return errors.New("sender not authorised to anchor proofs")
		}
		if len(m.Hash) != 64 {
			return errors.New("hash must be 32 bytes hex")
		}
		if _, dup := s.proofs[strings.ToLower(m.Hash)]; dup {
			return errors.New("proof already anchored")
		}
	default:
		return errors.New("unknown message type")
	}
	if bal < need {
		return ErrInsufficient
	}
	return nil
}

func validAddr(a string) bool { return strings.HasPrefix(a, "aur1") && len(a) > 4 }

// deliver applies a tx; caller holds mu and has run check().
func (s *State) deliver(tx Tx, height int64) TxResult {
	r := TxResult{Hash: tx.Hash(), Height: height, Type: string(tx.Msg.Type), From: tx.From}
	from := s.acct(tx.From)
	from.Nonce++
	from.Balance -= tx.Fee
	s.acct(s.feeCollector).Balance += tx.Fee
	m := tx.Msg
	switch m.Type {
	case TypeSend:
		from.Balance -= m.Amount
		s.acct(m.To).Balance += m.Amount
		r.To, r.Amount = m.To, m.Amount
	case TypeCreateEscrow:
		from.Balance -= m.Amount
		e := &Escrow{ID: s.nextEscrowID, Sender: tx.From, Recipient: m.To, Arbiter: m.Arbiter,
			Amount: m.Amount, DeadlineHeight: m.DeadlineHeight, Status: Locked, CreatedHeight: height}
		s.escrows[e.ID] = e
		s.nextEscrowID++
		r.To, r.Amount, r.EscrowID = m.To, m.Amount, e.ID
	case TypeReleaseEscrow:
		e := s.escrows[m.EscrowID]
		e.Status = Released
		s.acct(e.Recipient).Balance += e.Amount
		r.To, r.Amount, r.EscrowID = e.Recipient, e.Amount, e.ID
	case TypeRefundEscrow:
		e := s.escrows[m.EscrowID]
		e.Status = Refunded
		s.acct(e.Sender).Balance += e.Amount
		r.To, r.Amount, r.EscrowID = e.Sender, e.Amount, e.ID
	case TypeAnchorProof:
		h := strings.ToLower(m.Hash)
		s.proofs[h] = &PaymentProof{Hash: h, ExternalRef: m.ExternalRef, Height: height, Submitter: tx.From}
	}
	return r
}

// CommitBlock checks and delivers txs in order, producing a new block.
// Invalid txs are recorded with a non-zero code and have no effect.
func (s *State) CommitBlock(txs []Tx, protocolTime int64) Block {
	s.mu.Lock()
	defer s.mu.Unlock()
	h := s.height + 1
	s.height = h // escrow checks during this block see the new height
	results := make([]TxResult, 0, len(txs))
	for _, tx := range txs {
		if err := s.check(tx); err != nil {
			results = append(results, TxResult{Hash: tx.Hash(), Height: h, Type: string(tx.Msg.Type),
				From: tx.From, Code: 1, Log: err.Error()})
			continue
		}
		results = append(results, s.deliver(tx, h))
	}
	prev := s.blocks[len(s.blocks)-1].Hash
	sum := sha256.Sum256([]byte(fmt.Sprintf("%d|%s|%d|%v", h, prev, protocolTime, results)))
	b := Block{Height: h, Time: protocolTime, PrevHash: prev, Hash: strings.ToUpper(hex.EncodeToString(sum[:])), Txs: results}
	s.blocks = append(s.blocks, b)
	return b
}

// ---- queries ----

func (s *State) Height() int64 { s.mu.RLock(); defer s.mu.RUnlock(); return s.height }

func (s *State) Account(addr string) Account {
	s.mu.RLock()
	defer s.mu.RUnlock()
	if a, ok := s.accounts[addr]; ok {
		return *a
	}
	return Account{Address: addr}
}

func (s *State) Escrow(id uint64) (Escrow, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	e, ok := s.escrows[id]
	if !ok {
		return Escrow{}, false
	}
	return *e, true
}

func (s *State) Escrows() []Escrow {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := make([]Escrow, 0, len(s.escrows))
	for id := uint64(1); id < s.nextEscrowID; id++ {
		out = append(out, *s.escrows[id])
	}
	return out
}

func (s *State) Proof(hash string) (PaymentProof, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	p, ok := s.proofs[strings.ToLower(hash)]
	if !ok {
		return PaymentProof{}, false
	}
	return *p, true
}

// Blocks returns up to limit most recent blocks, newest first.
func (s *State) Blocks(limit int) []Block {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := []Block{}
	for i := len(s.blocks) - 1; i >= 0 && len(out) < limit; i-- {
		out = append(out, s.blocks[i])
	}
	return out
}

func (s *State) Block(h int64) (Block, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	if h < 0 || h >= int64(len(s.blocks)) {
		return Block{}, false
	}
	return s.blocks[h], true
}

func (s *State) TotalSupply() uint64 { s.mu.RLock(); defer s.mu.RUnlock(); return s.totalSupply }

// SumBalances and LockedInEscrow are used by invariant checks.
func (s *State) SumBalances() uint64 {
	s.mu.RLock()
	defer s.mu.RUnlock()
	var t uint64
	for _, a := range s.accounts {
		t += a.Balance
	}
	return t
}

func (s *State) LockedInEscrow() uint64 {
	s.mu.RLock()
	defer s.mu.RUnlock()
	var t uint64
	for _, e := range s.escrows {
		if e.Status == Locked {
			t += e.Amount
		}
	}
	return t
}
