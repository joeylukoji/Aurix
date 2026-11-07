package types

import (
	"fmt"

	sdk "github.com/cosmos/cosmos-sdk/types"
)

// DefaultAnchorThreshold is the number of attestations needed by default (single gateway).
const DefaultAnchorThreshold = 1

// DefaultParams returns no anchor signers and threshold 1.
func DefaultParams() Params {
	return Params{AnchorSigners: []string{}, AnchorThreshold: DefaultAnchorThreshold}
}

// Validate checks every signer is a valid, unique address and
// 1 <= anchor_threshold <= len(anchor_signers). With no signers the threshold is not
// constrained (anchoring is simply disabled).
func (p Params) Validate() error {
	seen := make(map[string]bool, len(p.AnchorSigners))
	for _, a := range p.AnchorSigners {
		if _, err := sdk.AccAddressFromBech32(a); err != nil {
			return fmt.Errorf("invalid anchor signer %q: %w", a, err)
		}
		if seen[a] {
			return fmt.Errorf("duplicate anchor signer %q", a)
		}
		seen[a] = true
	}
	if len(p.AnchorSigners) == 0 {
		return nil
	}
	if p.AnchorThreshold < 1 || int(p.AnchorThreshold) > len(p.AnchorSigners) {
		return fmt.Errorf("anchor_threshold %d must be within [1, %d]", p.AnchorThreshold, len(p.AnchorSigners))
	}
	return nil
}
