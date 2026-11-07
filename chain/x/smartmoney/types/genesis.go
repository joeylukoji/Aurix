package types

import (
	"fmt"

	sdk "github.com/cosmos/cosmos-sdk/types"
)

func DefaultGenesis() *GenesisState {
	return &GenesisState{Params: Params{}, NextEscrowId: 1}
}

func (gs GenesisState) Validate() error {
	for _, a := range gs.Params.AnchorSigners {
		if _, err := sdk.AccAddressFromBech32(a); err != nil {
			return fmt.Errorf("invalid anchor signer %q: %w", a, err)
		}
	}
	seen := map[uint64]bool{}
	for _, e := range gs.Escrows {
		if e.Id == 0 || e.Id >= gs.NextEscrowId || seen[e.Id] {
			return fmt.Errorf("invalid escrow id %d", e.Id)
		}
		seen[e.Id] = true
	}
	return nil
}
