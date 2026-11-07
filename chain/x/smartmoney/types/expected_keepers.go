package types

import (
	"context"

	sdk "github.com/cosmos/cosmos-sdk/types"
)

type BankKeeper interface {
	SendCoinsFromAccountToModule(ctx context.Context, sender sdk.AccAddress, module string, amt sdk.Coins) error
	SendCoinsFromModuleToAccount(ctx context.Context, module string, recipient sdk.AccAddress, amt sdk.Coins) error
	GetBalance(ctx context.Context, addr sdk.AccAddress, denom string) sdk.Coin
}
