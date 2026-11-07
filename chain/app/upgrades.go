package app

import (
	"context"
	"fmt"
	"strings"

	storetypes "cosmossdk.io/store/types"
	upgradetypes "cosmossdk.io/x/upgrade/types"
	"github.com/cosmos/cosmos-sdk/types/module"
)

// Upgrade describes a named on-chain software upgrade. A governance proposal containing
// MsgSoftwareUpgrade{plan.name = Name} halts the chain at plan.height; the new binary
// (which registers the handler below) then runs CreateUpgradeHandler at that height.
type Upgrade struct {
	Name                 string
	CreateUpgradeHandler func(*module.Manager, module.Configurator) upgradetypes.UpgradeHandler
	// StoreUpgrades lists KV stores added/renamed/deleted by this upgrade (applied when the
	// new binary first loads at the upgrade height).
	StoreUpgrades storetypes.StoreUpgrades
}

// Upgrades is the list of upgrades known by this binary.
var Upgrades = []Upgrade{
	{
		// v2: first planned upgrade. No state change yet: it only runs the module migrations
		// (none registered today, so it is a no-op) and proves the halt/swap/restart procedure.
		Name: "v2",
		CreateUpgradeHandler: func(mm *module.Manager, cfg module.Configurator) upgradetypes.UpgradeHandler {
			return func(ctx context.Context, _ upgradetypes.Plan, fromVM module.VersionMap) (module.VersionMap, error) {
				return mm.RunMigrations(ctx, cfg, fromVM)
			}
		},
	},
}

// DisabledUpgrades is a comma separated list of upgrade names whose handler is NOT registered.
// It is empty in release builds. scripts/upgrade-drill.sh sets it at link time
// (-ldflags "-X github.com/joeylukoji/aurix/chain/app.DisabledUpgrades=v2") to build a
// stand-in for the "old" binary, which must halt at the upgrade height.
var DisabledUpgrades = ""

func upgradeDisabled(name string) bool {
	for _, d := range strings.Split(DisabledUpgrades, ",") {
		if strings.TrimSpace(d) == name {
			return true
		}
	}
	return false
}

func (app *App) registerUpgradeHandlers() {
	for _, u := range Upgrades {
		if upgradeDisabled(u.Name) {
			continue
		}
		app.UpgradeKeeper.SetUpgradeHandler(u.Name, u.CreateUpgradeHandler(app.ModuleManager, app.configurator))
	}

	// Store loader hook: if the node restarts at a pending upgrade height with the new binary,
	// apply that upgrade's store additions/removals before loading.
	info, err := app.UpgradeKeeper.ReadUpgradeInfoFromDisk()
	if err != nil {
		panic(fmt.Sprintf("failed to read upgrade info from disk: %s", err))
	}
	if info.Name == "" || app.UpgradeKeeper.IsSkipHeight(info.Height) {
		return
	}
	for _, u := range Upgrades {
		su := u.StoreUpgrades
		if u.Name == info.Name && (len(su.Added) > 0 || len(su.Renamed) > 0 || len(su.Deleted) > 0) {
			app.SetStoreLoader(upgradetypes.UpgradeStoreLoader(info.Height, &su))
		}
	}
}
