
var KrempelAirApp = angular.module('KrempelAirApp', []);
var uri = "/api"
KrempelAirApp.controller('AirFlowController', function AirFlowController($scope, $http, $timeout) {


    $scope.Refresh = function(){
        $http.get(uri+"/").then(function (response) {
            $scope.status = response.data;
        });
        $http.get(uri+"/stoerung").then(function (response) {
            $scope.stoerung = response.data;
        });
        $http.get(uri+"/lueftung/temperatur").then(function (response) {
            $scope.temperatur = response.data;
        });
        if ($scope.sollTemp) {
            $http.get(uri+"/lueftung/temperatur/"+parseFloat($scope.sollTemp));
        }
        
        $timeout(function(){
         $scope.Refresh();
        },800);
    }
    $scope.Klick = function(Klick){
        $http.get(uri+"/lueftung/stufe/"+Klick);
        $scope.Refresh();
    }
    
    $scope.Raucher = function(value){
        if(value == 0){
            $http.get(uri+"/raucherraum/off");
        }else{
            $http.get(uri+"/raucherraum/on");
        }    
        $scope.Refresh();
    }
    
    $scope.Refresh();
});
