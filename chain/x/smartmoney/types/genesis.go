package types

import (
	"encoding/hex"
	"fmt"
	"strings"

	sdk "github.com/cosmos/cosmos-sdk/types"
)

func DefaultGenesis() *GenesisState {
	return &GenesisState{Params: DefaultParams(), NextEscrowId: 1}
}

func (gs GenesisState) Validate() error {
	if err := gs.Params.Validate(); err != nil {
		return err
	}
	seen := map[uint64]bool{}
	for _, e := range gs.Escrows {
		if e.Id == 0 || e.Id >= gs.NextEscrowId || seen[e.Id] {
			return fmt.Errorf("invalid escrow id %d", e.Id)
		}
		seen[e.Id] = true
	}
	hashes := map[string]bool{}
	for _, p := range gs.Proofs {
		if b, err := hex.DecodeString(p.Hash); err != nil || len(b) != 32 || p.Hash != strings.ToLower(p.Hash) {
			return fmt.Errorf("invalid proof hash %q", p.Hash)
		}
		if hashes[p.Hash] {
			return fmt.Errorf("duplicate proof %s", p.Hash)
		}
		hashes[p.Hash] = true
		att := map[string]bool{}
		for _, a := range p.Attestations {
			if _, err := sdk.AccAddressFromBech32(a); err != nil {
				return fmt.Errorf("proof %s: invalid attestor %q", p.Hash, a)
			}
			if att[a] {
				return fmt.Errorf("proof %s: duplicate attestor %q", p.Hash, a)
			}
			att[a] = true
		}
		if p.Amount != "" || p.Currency != "" {
			if err := ValidateProofAmount(p.Amount, p.Currency); err != nil {
				return fmt.Errorf("proof %s: %w", p.Hash, err)
			}
		}
		if p.ExpiresHeight < 0 {
			return fmt.Errorf("proof %s: negative expires_height", p.Hash)
		}
		if p.Status == PROOF_STATUS_CONFIRMED && p.ConfirmedHeight <= 0 {
			return fmt.Errorf("proof %s: confirmed without confirmed_height", p.Hash)
		}
	}
	return nil
}
