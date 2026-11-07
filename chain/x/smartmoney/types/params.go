package types

import (
	"fmt"
	"regexp"
	"strings"

	sdk "github.com/cosmos/cosmos-sdk/types"
)

// DefaultAnchorThreshold is the number of attestations needed by default (single gateway).
const DefaultAnchorThreshold = 1

// DefaultPendingTTLBlocks is how long a PENDING proof lives before EndBlocker deletes it.
const DefaultPendingTTLBlocks = 600

// DefaultParams returns no anchor signers, threshold 1 and a 600-block pending TTL.
func DefaultParams() Params {
	return Params{AnchorSigners: []string{}, AnchorThreshold: DefaultAnchorThreshold, PendingTtlBlocks: DefaultPendingTTLBlocks}
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

var currencyRe = regexp.MustCompile(`^[A-Z]{3}$`)
var amountRe = regexp.MustCompile(`^[0-9]+(\.[0-9]{1,2})?$`)

// ValidateProofAmount checks amount is a positive decimal with at most 2 decimals and
// currency is 3 uppercase letters (ISO 4217).
func ValidateProofAmount(amount, currency string) error {
	if amount == "" || !amountRe.MatchString(amount) || strings.Trim(strings.ReplaceAll(amount, ".", ""), "0") == "" {
		return fmt.Errorf("amount %q must be a positive decimal with at most 2 decimals", amount)
	}
	if !currencyRe.MatchString(currency) {
		return fmt.Errorf("currency %q must be 3 uppercase letters", currency)
	}
	return nil
}
