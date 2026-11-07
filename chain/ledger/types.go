// Package ledger implements the AURIX state machine: the x/aurixbank and
// x/smartmoney logic described in AURIX_SOFTWARE_ARCHITECTURE.md.
// It is deterministic and has no dependency on wall-clock time, PayPal or AI.
package ledger

import (
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"strings"
)

// Amounts are integers in uaur (1 AUR = 1_000_000 uaur).
const (
	MicroPerAUR = 1_000_000
	DefaultFee  = 1_000 // 0.001 AUR
)

type MsgType string

const (
	TypeSend          MsgType = "MsgSend"
	TypeCreateEscrow  MsgType = "MsgCreateEscrow"
	TypeReleaseEscrow MsgType = "MsgReleaseEscrow"
	TypeRefundEscrow  MsgType = "MsgRefundEscrow"
	TypeAnchorProof   MsgType = "MsgAnchorProof"
)

type EscrowStatus string

const (
	Locked   EscrowStatus = "LOCKED"
	Released EscrowStatus = "RELEASED"
	Refunded EscrowStatus = "REFUNDED"
)

type Account struct {
	Address string `json:"address"`
	Nonce   uint64 `json:"nonce"`
	Balance uint64 `json:"balance"`
}

type Escrow struct {
	ID             uint64       `json:"id"`
	Sender         string       `json:"sender"`
	Recipient      string       `json:"recipient"`
	Arbiter        string       `json:"arbiter"`
	Amount         uint64       `json:"amount"`
	DeadlineHeight int64        `json:"deadline_height"`
	Status         EscrowStatus `json:"status"`
	CreatedHeight  int64        `json:"created_height"`
}

type PaymentProof struct {
	Hash        string `json:"hash"`
	ExternalRef string `json:"external_ref"`
	Height      int64  `json:"height"`
	Submitter   string `json:"submitter"`
}

// Msg is the payload of a transaction; fields used depend on Type.
type Msg struct {
	Type           MsgType `json:"type"`
	To             string  `json:"to,omitempty"`
	Amount         uint64  `json:"amount,omitempty"`
	Arbiter        string  `json:"arbiter,omitempty"`
	DeadlineHeight int64   `json:"deadline_height,omitempty"`
	EscrowID       uint64  `json:"escrow_id,omitempty"`
	Hash           string  `json:"hash,omitempty"`
	ExternalRef    string  `json:"external_ref,omitempty"`
}

// Tx is a signed transaction. Signature covers SignBytes().
type Tx struct {
	Msg       Msg    `json:"msg"`
	From      string `json:"from"`
	PubKey    string `json:"pub_key"` // hex ed25519
	Nonce     uint64 `json:"nonce"`
	Fee       uint64 `json:"fee"`
	Signature string `json:"signature"` // hex
}

type signDoc struct {
	ChainID string `json:"chain_id"`
	Msg     Msg    `json:"msg"`
	From    string `json:"from"`
	Nonce   uint64 `json:"nonce"`
	Fee     uint64 `json:"fee"`
}

// SignBytes returns the canonical bytes to sign (deterministic JSON).
func (t Tx) SignBytes(chainID string) []byte {
	b, _ := json.Marshal(signDoc{chainID, t.Msg, t.From, t.Nonce, t.Fee})
	return b
}

func (t Tx) Hash() string {
	b, _ := json.Marshal(t)
	h := sha256.Sum256(b)
	return strings.ToUpper(hex.EncodeToString(h[:]))
}

// AddressFromPubKey derives "aur1" + hex(sha256(pub)[:20]).
func AddressFromPubKey(pub ed25519.PublicKey) string {
	h := sha256.Sum256(pub)
	return "aur1" + hex.EncodeToString(h[:20])
}

// Sign fills From, PubKey and Signature.
func (t *Tx) Sign(priv ed25519.PrivateKey, chainID string) {
	pub := priv.Public().(ed25519.PublicKey)
	t.From = AddressFromPubKey(pub)
	t.PubKey = hex.EncodeToString(pub)
	t.Signature = hex.EncodeToString(ed25519.Sign(priv, t.SignBytes(chainID)))
}

func (t Tx) VerifySignature(chainID string) error {
	pub, err := hex.DecodeString(t.PubKey)
	if err != nil || len(pub) != ed25519.PublicKeySize {
		return errors.New("invalid public key")
	}
	if AddressFromPubKey(pub) != t.From {
		return errors.New("public key does not match sender address")
	}
	sig, err := hex.DecodeString(t.Signature)
	if err != nil || !ed25519.Verify(pub, t.SignBytes(chainID), sig) {
		return errors.New("invalid signature")
	}
	return nil
}

type TxResult struct {
	Hash     string `json:"hash"`
	Height   int64  `json:"height"`
	Type     string `json:"type"`
	From     string `json:"from"`
	To       string `json:"to,omitempty"`
	Amount   uint64 `json:"amount,omitempty"`
	EscrowID uint64 `json:"escrow_id,omitempty"`
	Code     int    `json:"code"`
	Log      string `json:"log,omitempty"`
}

type Block struct {
	Height   int64      `json:"height"`
	Time     int64      `json:"time"` // protocol time, set by the proposer
	PrevHash string     `json:"prev_hash"`
	Hash     string     `json:"hash"`
	Txs      []TxResult `json:"txs"`
}
