package types

import "cosmossdk.io/collections"

const (
	ModuleName = "smartmoney"
	StoreKey   = ModuleName
	// Denom is the native AURIX coin denomination (1 AUR = 1_000_000 uaur).
	Denom = "uaur"
)

var (
	ParamsKey       = collections.NewPrefix(0)
	EscrowsKey      = collections.NewPrefix(1)
	NextEscrowIDKey = collections.NewPrefix(2)
	ProofsKey       = collections.NewPrefix(3)
)
