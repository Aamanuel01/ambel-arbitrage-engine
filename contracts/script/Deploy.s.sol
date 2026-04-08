// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import {Script, console} from "forge-std/Script.sol";
import {ArbitrageBot} from "../src/ArbitrageBot.sol";

/// @notice Deployment script for ArbitrageBot.
///         Usage:
///   forge script script/Deploy.s.sol \
///     --rpc-url $POLYGON_WS_RPC_URL \
///     --private-key $DEPLOYER_PRIVATE_KEY \
///     --broadcast
contract DeployArbitrageBot is Script {
    // ── Polygon Mainnet: Aave V3 Pool Addresses Provider ─────────────────────────
    // DEX router addresses are NOT hardcoded here — they are passed per-call by
    // the Python bot via the `dexPayload` argument to `requestFlashLoan`.
    address constant AAVE_ADDRESSES_PROV = 0xa97684ead0e402dC232d5A977953DF7ECBaB3CDb;

    function run() external {
        uint256 deployerKey = vm.envUint("DEPLOYER_PRIVATE_KEY");
        vm.startBroadcast(deployerKey);

        ArbitrageBot bot = new ArbitrageBot(AAVE_ADDRESSES_PROV);

        console.log("ArbitrageBot deployed at:", address(bot));
        console.log("Aave Pool (resolved):", address(bot.POOL()));

        vm.stopBroadcast();
    }
}
